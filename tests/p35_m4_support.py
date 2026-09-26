"""Authored synthetic fixtures for P35 M4 comparison tests.

Every number here is SYNTHETIC: authored to exercise comparison logic, never a
training, benchmark or timing result. Evidence records use the
``xlm-science-run-evidence-v2`` shape (M5 adds the membership field) produced by
``extract_run_evidence``;
``tests/test_p35_m4_evidence.py`` separately derives that shape from authored
checkpoint artifacts published through the real ``ArtifactStore``.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.comparison.science_compare import RunEntry
from xlm.comparison.science_evidence import EVIDENCE_VERSION
from xlm.comparison.science_manifest import MEMBERSHIP_SENTINEL, ORDER_SENTINEL

SCREEN_BUDGET = 128_000_000
FULL_BUDGET_50M = 1_000_000_000
PRIMARY = "equal_domain_text_ce_nats_per_token"

#: §H roster (contract table), exploratory and confirmation blocks.
E_TUPLES = [
    ("E0", 101, 10001, 20260918),
    ("E1", 211, 10002, 20260919),
    ("E2", 307, 10003, 20260920),
]
C_TUPLES = [
    ("C0", 401, 20001, 20261001),
    ("C1", 503, 20002, 20261002),
    ("C2", 601, 20003, 20261003),
    ("C3", 701, 20004, 20261004),
    ("C4", 809, 20005, 20261005),
]

M0_COMPONENTS = [
    {"source_id": "prose", "weight": 0.5},
    {"source_id": "science", "weight": 0.3},
    {"source_id": "web", "weight": 0.2},
]
M1_COMPONENTS = [
    {"source_id": "prose", "weight": 0.4},
    {"source_id": "science", "weight": 0.4},
    {"source_id": "web", "weight": 0.2},
]


def h(label: str) -> str:
    """A synthetic 64-hex identity for an authored label."""
    return hashlib.sha256(f"synthetic:{label}".encode()).hexdigest()


def quick_thresholds(budget: int) -> list[int]:
    if budget == SCREEN_BUDGET:
        return [
            0,
            1_000_000,
            4_000_000,
            8_000_000,
            16_000_000,
            32_000_000,
            64_000_000,
            96_000_000,
            128_000_000,
        ]
    return [
        0,
        16_000_000,
        32_000_000,
        64_000_000,
        128_000_000,
        256_000_000,
        512_000_000,
        768_000_000,
        1_000_000_000,
    ]


def actual(threshold: int) -> int:
    """First 65,536-target boundary at or above a threshold (§K first crossing)."""
    step = 65_536
    return min(-(-threshold // step) * step, 10**12) if threshold else 0


def base_fields(
    *,
    run_id: str,
    seeds: tuple[int, int, int],
    budget: int,
    microbatch: int = 8,
    components: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    components = copy.deepcopy(components or M0_COMPONENTS)
    mixture_key = identity_digest(components)
    return {
        "model_architecture": {
            "architecture": "reference_decoder",
            "num_layers": 10,
            "hidden_size": 512,
            "num_attention_heads": 8,
            "intermediate_size": 1472,
            "vocab_size": 32768,
            "context_length": 512,
            "dropout": 0.0,
        },
        "model_component": {"key": "reference_decoder", "serializer": "1"},
        "model_parameter_count": 49_883_648,
        "attention_backend": "sdpa",
        "initial_model_state_digest": h(f"init-weights-{seeds[0]}"),
        "objective": {
            "config": {"type": "cross_entropy", "version": "1"},
            "component": {"key": "ce"},
        },
        "init_seed": seeds[0],
        "training_seed": seeds[1],
        "data_seed": seeds[2],
        "order_manifest_id": ORDER_SENTINEL,
        "within_source_order_policy": "shard_native_offset_order_v1",
        "canonical_membership_id": MEMBERSHIP_SENTINEL,
        "tokenizer_identity": {"fingerprint": h("tokenizer-bpe-32768")},
        "vocab_size": 32768,
        "data_input_identity": {"pool": h("pool-m0")},
        "mixture_components": components,
        "mixture_exhaustion_policy": {"repeat": False, "max_epochs": 1},
        "mixture_scheduler": "token_deficit_v1",
        "packing_policy": {"mode": "causal_stream", "cross_document_attention": True},
        "context_length": 512,
        "exposure_plan_identity": h(f"exposure-{mixture_key}-{budget}"),
        "data_trace_digest": h(f"trace-{mixture_key}-{seeds[2]}-{budget}"),
        "per_source_exposure": {
            c["source_id"]: {"valid_targets": int(c["weight"] * budget), "repeated_targets": 0}
            for c in components
        },
        "global_batch_valid_targets": 65_536,
        "microbatch_sequences": microbatch,
        "budget_targets": budget,
        "committed_targets": budget,
        "update_boundaries_digest": h(f"updates-{budget}-65536"),
        "optimizer": {
            "config": {
                "type": "adamw",
                "lr": 0.001,
                "betas": [0.9, 0.95],
                "eps": 1e-8,
                "weight_decay": 0.1,
            },
            "component": {"key": "adamw"},
        },
        "gradient_clip_norm": 1.0,
        "schedule": {
            "config": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 10_000_000,
                "horizon_valid_targets": 1_000_000_000,
                "min_lr_ratio": 0.1,
            },
            "component": {"key": "warmup_cosine"},
        },
        "lr_policy": "target_endpoint_before_update_v1",
        "science_version": "xlm-science-v1",
        "precision": "bf16_fp32_master",
        "runtime_policy": {
            "attention_policy": "statistical_efficient_v1",
            "matmul_tf32": "disabled",
            "bf16_reduced_precision_reduction": "allowed",
        },
        "compile": False,
        "activation_checkpointing": False,
        "producer_prefetch": "process_depth1",
        "device": "cuda",
        "runtime_device_name": ["SYNTHETIC GPU"],
        "runtime_torch": ["2.14.0+cu126"],
        "code_hash": h("code"),
        "dependency_hash": h("lock"),
        "environment_digest": h("environment"),
        "evaluation_plan_digest": h(f"evaluation-plan-{budget}"),
        "evaluator_identities": {"full_lm": h("full-evaluator"), "quick_lm": h("quick-evaluator")},
        "checkpoint_plan_digest": h(f"checkpoint-plan-{budget}"),
        "run_id": run_id,
        "plan_hash": h(f"plan-{run_id}"),
        "execution_hash": h(f"execution-{run_id}"),
        "checkpoint_id": f"{run_id}_ckpt-t{budget}-a001",
        "final_model_state_digest": h(f"final-{run_id}"),
        "observed_attention_ops": ["aten::_efficient_attention_backward"],
    }


def make_evidence(
    run_id: str,
    seeds: tuple[int, int, int],
    *,
    primary: float,
    budget: int = SCREEN_BUDGET,
    microbatch: int = 8,
    components: list[dict[str, Any]] | None = None,
    overrides: Mapping[str, Any] | None = None,
    endpoint_tier: str = "full_lm",
    secondary: Mapping[str, float] | None = None,
    curve: Mapping[int, float] | None = None,
    at_budget: bool = True,
    endpoint_complete: bool = True,
    order_manifest_id: str = ORDER_SENTINEL,
    canonical_membership_id: str | None = None,
) -> dict[str, Any]:
    """A SYNTHETIC evidence record in the extractor's shape."""
    fields = base_fields(
        run_id=run_id, seeds=seeds, budget=budget, microbatch=microbatch, components=components
    )
    fields["order_manifest_id"] = order_manifest_id
    if order_manifest_id != ORDER_SENTINEL:
        # An M5-ordered run carries its order's membership and the M5 policy.
        assert canonical_membership_id is not None, "an M5 order needs its membership"
        fields["canonical_membership_id"] = canonical_membership_id
        fields["within_source_order_policy"] = "m5_within_source_document_permutation_v1"
    fields.update(copy.deepcopy(dict(overrides or {})))
    committed = fields["committed_targets"] if at_budget else budget // 2
    fields["committed_targets"] = committed
    events: dict[str, Any] = {}
    for threshold in quick_thresholds(budget):
        value = (curve or {}).get(threshold, primary + 1.0 + (budget - threshold) / budget)
        events[f"quick_lm@{threshold}"] = _event("quick_lm", threshold, budget, {PRIMARY: value})
    endpoint_metrics: dict[str, Any] = {
        PRIMARY: primary,
        "equal_domain_text_bpb": primary / 2.0,
        "domains": {
            name: {"text_ce_nats_per_token": primary + offset}
            for name, offset in (("prose", -0.1), ("science", 0.0), ("web", 0.1))
        },
    }
    for name, value in (secondary or {}).items():
        endpoint_metrics[name] = value
    for tier in ("full_lm", endpoint_tier):
        event = _event(tier, budget, budget, endpoint_metrics)
        if not endpoint_complete and tier == endpoint_tier:
            event.update(
                complete=False,
                reasons=["no complete attempt in the crossing lineage"],
                metrics=None,
                coverage_complete=False,
            )
        events[f"{tier}@{budget}"] = event
    record: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "source": {"kind": "authored_fixture", "synthetic": True},
        "replicate": {
            "init_seed": fields["init_seed"],
            "training_seed": fields["training_seed"],
            "data_seed": fields["data_seed"],
            "order_manifest_id": fields["order_manifest_id"],
        },
        "fields": fields,
        "endpoint": {
            "committed_targets": committed,
            "budget_targets": fields["budget_targets"],
            "step": committed // 65_536,
            "updates": committed // 65_536,
            "at_budget": committed == fields["budget_targets"],
        },
        "evaluations": events,
    }
    record["evidence_digest"] = identity_digest(record)
    return record


def _event(tier: str, threshold: int, budget: int, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "tier": tier,
        "planned_threshold": threshold,
        "is_endpoint": threshold == budget,
        "actual_committed_targets": actual(threshold) if threshold != budget else budget,
        "complete": True,
        "reasons": [],
        "canonical_attempt": f"ev_synthetic_{tier}_{threshold}_a001_outcome",
        "receipt_identity": h(f"receipt-{tier}-{threshold}"),
        "evaluator": h(f"{tier}-evaluator"),
        "metrics": copy.deepcopy(metrics),
        "coverage_complete": True,
        "attempts": [{"number": 1, "status": "complete", "in_lineage": True}],
    }


def run(
    label: str,
    arm_id: str,
    evidence: Mapping[str, Any] | None,
    *,
    status: str = "completed",
    failure: str | None = None,
    measurements: Mapping[str, Mapping[str, Any]] | None = None,
) -> RunEntry:
    return RunEntry(
        label=label,
        arm_id=arm_id,
        status=status,
        failure=failure,
        evidence=evidence,
        measurements=dict(measurements or {}),
        synthetic=True,
    )


def throughput(value: float) -> dict[str, dict[str, Any]]:
    return {
        "successful_targets_per_second": {"value": value, "source": "SYNTHETIC authored"},
        "peak_vram_gib": {"value": 11.0, "source": "SYNTHETIC authored"},
    }


def _roster(tuples: list[tuple[str, int, int, int]], role: str) -> list[dict[str, Any]]:
    return [
        {
            "tuple_id": t,
            "role": role,
            "init_seed": i,
            "training_seed": r,
            "data_seed": d,
            "order_manifest_id": ORDER_SENTINEL,
        }
        for t, i, r, d in tuples
    ]


def _common(track: str, invariants_extra: tuple[str, ...] = ()) -> dict[str, Any]:
    from xlm.comparison.science_tracks import get_track

    track_obj = get_track(track)
    assert track_obj is not None
    return {
        "science_comparison_version": "xlm-science-comparison-v1",
        "status": "frozen",
        "preregistered_at": "2026-09-26T00:00:00Z",
        "parent_baseline": {"id": "xlm-50m-baseline-dev", "version": "synthetic-0"},
        "track": track,
        "required_invariants": [*track_obj.must_match(), *invariants_extra],
        "fixed_values": {
            "global_batch_valid_targets": 65_536,
            "context_length": 512,
            "precision": "bf16_fp32_master",
            "science_version": "xlm-science-v1",
        },
        "schedule": {
            "lr_policy": "target_endpoint_before_update_v1",
            "base_lr": 0.001,
            "warmup_targets": 10_000_000,
            "horizon_targets": 1_000_000_000,
            "min_lr_ratio": 0.1,
        },
        "primary_metric": {"name": PRIMARY, "direction": "lower_is_better"},
        "margin_rationale": "SYNTHETIC decision record DR-0 (test fixture)",
        "ci_level": 0.95,
        "stopping_rule": "fixed_n_all_pairs_no_early_success_v1",
        "promotion_rule_version": "xlm-p35-promotion-v1",
        "final_checkpoint_requirement": "exact_budget_endpoint_v1",
        "failure_policy": "failed_or_missing_pair_blocks_confirmation_v1",
        "order_robustness": {"required": False, "m5_order_evidence": None},
        "scale_promotion": {
            "intent": False,
            "prerequisite": None,
            "resource_plan_ref": None,
            "ablation_refs": [],
        },
    }


def microbatch_manifest(
    *, stage: str = "screen", tuples: list[tuple[str, int, int, int]] | None = None
) -> dict[str, Any]:
    confirmation = stage == "confirmation"
    budget = FULL_BUDGET_50M if confirmation else SCREEN_BUDGET
    doc = _common("microbatch_grouping_v1")
    doc.update(
        {
            "comparison_id": f"synthetic-microbatch-{stage}",
            "title": "SYNTHETIC B8 vs B16/B32 grouping",
            "description": "Authored fixture; no results.",
            "scale_stage": "50m",
            "study_stage": stage,
            "question": "noninferiority",
            "control_arm": {
                "arm_id": "b8",
                "label": "B8",
                "intervention": {"microbatch_sequences": 8},
            },
            "candidate_arms": [
                {"arm_id": "b16", "label": "B16", "intervention": {"microbatch_sequences": 16}},
                {"arm_id": "b32", "label": "B32", "intervention": {"microbatch_sequences": 32}},
            ],
            "replicate_roster": _roster(
                tuples or (C_TUPLES if confirmation else E_TUPLES),
                "confirmation" if confirmation else "exploratory",
            ),
            "intended_differences": ["microbatch_sequences"],
            "training_budget_targets": budget,
            "primary_endpoint": {
                "tier": "endpoint_confirmation" if confirmation else "full_lm",
                "planned_threshold": budget,
            },
            "secondary_metrics": [],
            "curve_metric": {
                "name": PRIMARY,
                "direction": "lower_is_better",
                "tier": "quick_lm",
                "start_target": 16_000_000,
                "end_target": budget,
            },
            "practical_margin": None,
            "noninferiority_margin": 0.02,
            "efficiency": {
                "name": "successful_targets_per_second",
                "direction": "higher_is_better",
                "minimum_relative_gain": 0.05,
            },
            "confirmatory_pairs": 5 if confirmation else None,
            "multiplicity": {
                "family_id": "synthetic-batch-family",
                "family_size": 2,
                "guardrail_family_size": 0,
            },
        }
    )
    return doc


def superiority_manifest(
    *,
    stage: str = "confirmation",
    scale: str = "50m",
    tuples: list[tuple[str, int, int, int]] | None = None,
    margin: float | None = 0.01,
    family_size: int = 1,
) -> dict[str, Any]:
    """A mixture-track superiority question (M0 control vs M1 candidate)."""
    confirmation = stage == "confirmation"
    budget = {"50m": FULL_BUDGET_50M, "150m": 3_000_000_000, "300m": 6_000_000_000}[scale]
    if not confirmation:
        budget = SCREEN_BUDGET
    doc = _common("data_mixture_v1")
    roster_tuples = tuples or (
        C_TUPLES[: 5 if scale == "50m" else 3] if confirmation else E_TUPLES[:1]
    )
    doc.update(
        {
            "comparison_id": f"synthetic-mixture-{scale}-{stage}",
            "title": "SYNTHETIC M0 vs M1 mixture",
            "description": "Authored fixture; no results.",
            "scale_stage": scale,
            "study_stage": stage,
            "question": "superiority",
            "control_arm": {
                "arm_id": "m0",
                "label": "M0",
                "intervention": {"mixture_components": copy.deepcopy(M0_COMPONENTS)},
            },
            "candidate_arms": [
                {
                    "arm_id": "m1",
                    "label": "M1",
                    "intervention": {"mixture_components": copy.deepcopy(M1_COMPONENTS)},
                }
            ],
            "replicate_roster": _roster(
                roster_tuples, "confirmation" if confirmation else "exploratory"
            ),
            "intended_differences": [
                "mixture_components",
                "exposure_plan_identity",
                "data_trace_digest",
                "per_source_exposure",
            ],
            "training_budget_targets": budget,
            "primary_endpoint": {
                "tier": "endpoint_confirmation" if confirmation else "full_lm",
                "planned_threshold": budget,
            },
            "secondary_metrics": [
                {
                    "name": "domain_web_text_ce_nats_per_token",
                    "direction": "lower_is_better",
                    "role": "guardrail",
                    "source": {
                        "tier": "full_lm",
                        "planned_threshold": budget,
                        "path": ["domains", "web", "text_ce_nats_per_token"],
                    },
                    "regression_margin": 0.05,
                }
            ],
            "curve_metric": None,
            "practical_margin": margin,
            "noninferiority_margin": None,
            "efficiency": None,
            "confirmatory_pairs": ({"50m": 5}.get(scale, 3)) if confirmation else None,
            "multiplicity": {
                "family_id": "synthetic-mixture-family",
                "family_size": family_size,
                "guardrail_family_size": 1,
            },
        }
    )
    return doc


def declared_membership(manifest: Mapping[str, Any]) -> str | None:
    """The membership an M5 order declaration names (runs under it train on it)."""
    evidence = (manifest.get("order_robustness") or {}).get("m5_order_evidence")
    if isinstance(evidence, Mapping):
        value = evidence.get("canonical_membership_id")
        return str(value) if value is not None else None
    return None


def mixture_pair_runs(
    manifest: Mapping[str, Any],
    control_values: Mapping[str, float],
    candidate_values: Mapping[str, float],
    *,
    candidate_overrides: Mapping[str, Any] | None = None,
) -> list[RunEntry]:
    """Paired M0/M1 runs over the manifest roster with SYNTHETIC primary values."""
    budget = manifest["training_budget_targets"]
    tier = manifest["primary_endpoint"]["tier"]
    runs: list[RunEntry] = []
    for entry in manifest["replicate_roster"]:
        t = entry["tuple_id"]
        seeds = (entry["init_seed"], entry["training_seed"], entry["data_seed"])
        for arm, components, values, overrides in (
            ("m0", M0_COMPONENTS, control_values, None),
            ("m1", M1_COMPONENTS, candidate_values, candidate_overrides),
        ):
            if t not in values:
                continue
            evidence = make_evidence(
                f"run-{arm}-{t}",
                seeds,
                primary=values[t],
                budget=budget,
                components=components,
                endpoint_tier=tier,
                overrides=overrides,
                order_manifest_id=entry["order_manifest_id"],
                canonical_membership_id=declared_membership(manifest),
            )
            runs.append(run(f"{arm}-{t}", arm, evidence))
    return runs


def microbatch_runs(
    manifest: Mapping[str, Any],
    values: Mapping[str, Mapping[str, float]],
    *,
    speeds: Mapping[str, float] | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[RunEntry]:
    budget = manifest["training_budget_targets"]
    tier = manifest["primary_endpoint"]["tier"]
    runs: list[RunEntry] = []
    for entry in manifest["replicate_roster"]:
        t = entry["tuple_id"]
        seeds = (entry["init_seed"], entry["training_seed"], entry["data_seed"])
        for arm, size in (("b8", 8), ("b16", 16), ("b32", 32)):
            if t not in values.get(arm, {}):
                continue
            evidence = make_evidence(
                f"run-{arm}-{t}",
                seeds,
                primary=values[arm][t],
                budget=budget,
                microbatch=size,
                endpoint_tier=tier,
                overrides=(overrides or {}).get(arm),
                order_manifest_id=entry["order_manifest_id"],
                canonical_membership_id=declared_membership(manifest),
            )
            runs.append(
                run(
                    f"{arm}-{t}",
                    arm,
                    evidence,
                    measurements=throughput((speeds or {}).get(arm, 45_000.0)),
                )
            )
    return runs
