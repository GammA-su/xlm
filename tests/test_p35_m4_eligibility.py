"""P35 M4: eligibility field diffs, pairing, replicate identity, tracks (SYNTHETIC evidence)."""

from __future__ import annotations

import copy
import random
from typing import Any

import pytest

from p35_m4_support import (
    C_TUPLES,
    E_TUPLES,
    M1_COMPONENTS,
    h,
    make_evidence,
    microbatch_manifest,
    microbatch_runs,
    mixture_pair_runs,
    run,
    superiority_manifest,
)
from xlm.comparison.science_compare import (
    ComparisonError,
    compare_science,
    verify_comparison_record,
)

CTRL = {"C0": 3.00, "C1": 3.10, "C2": 3.20, "C3": 3.05, "C4": 3.15}
CAND = {"C0": 2.98, "C1": 3.07, "C2": 3.19, "C3": 3.02, "C4": 3.12}
SCREEN = {"E0": 3.40, "E1": 3.45, "E2": 3.38}


def _candidate(record: dict[str, Any], arm: str = "m1") -> dict[str, Any]:
    return next(c for c in record["candidates"] if c["arm_id"] == arm)


def _violations(candidate: dict[str, Any]) -> set[str]:
    return {v["field"] for v in candidate["field_violations"]}


def _mixture(overrides: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND, candidate_overrides=overrides)
    return compare_science(manifest, runs, **kwargs)


def test_identical_arms_except_declared_intervention_are_eligible() -> None:
    record = _mixture()
    verify_comparison_record(record)
    candidate = _candidate(record)
    assert candidate["eligible"], candidate["ineligible_reasons"]
    assert candidate["pairing"]["n_complete"] == 5
    diff = {d["field"]: d for d in candidate["pairing"]["pairs"][0]["field_diff"]}
    assert diff["mixture_components"]["status"] == "declared_difference"
    assert diff["mixture_components"]["candidate"] == M1_COMPONENTS
    assert diff["per_source_exposure"]["status"] == "declared_difference"
    assert diff["per_source_exposure"]["control"]["prose"]["valid_targets"] == 500_000_000
    assert diff["tokenizer_identity"]["status"] == "match"
    assert diff["final_model_state_digest"]["status"] == "recorded_difference"
    assert candidate["decision"]["result"] == "CLEAR_WIN"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        (
            {"model_architecture": {"architecture": "reference_decoder", "num_layers": 12}},
            "model_architecture",
        ),
        ({"model_parameter_count": 60_000_000}, "model_parameter_count"),
        ({"attention_backend": "eager"}, "attention_backend"),
        (
            {
                "runtime_policy": {
                    "attention_policy": "strict_deterministic_v1",
                    "matmul_tf32": "disabled",
                    "bf16_reduced_precision_reduction": "allowed",
                }
            },
            "runtime_policy",
        ),
        (
            {
                "optimizer": {
                    "config": {"type": "adamw", "lr": 0.001, "betas": [0.9, 0.99]},
                    "component": {"key": "adamw"},
                }
            },
            "optimizer",
        ),
        ({"within_source_order_policy": "shuffled_documents_v2"}, "within_source_order_policy"),
        (
            {"mixture_exhaustion_policy": {"repeat": True, "max_epochs": 2}},
            "mixture_exhaustion_policy",
        ),
        ({"update_boundaries_digest": h("other-updates")}, "update_boundaries_digest"),
        ({"evaluator_identities": {"full_lm": h("other-inventory")}}, "evaluator_identities"),
        ({"environment_digest": h("other-environment")}, "environment_digest"),
    ],
)
def test_undeclared_material_difference_is_ineligible_with_the_field(
    overrides: dict[str, Any], field: str
) -> None:
    record = _mixture(overrides)
    candidate = _candidate(record)
    assert not candidate["eligible"]
    assert field in _violations(candidate)
    assert candidate["statistics"] is None, "no effect estimate for an ineligible comparison"
    assert candidate["decision"]["result"] == "INELIGIBLE"
    assert candidate["promotion"]["state"] == "NOT_ELIGIBLE"
    row = next(v for v in candidate["field_violations"] if v["field"] == field)
    assert row["status"] == "VIOLATION" and row["reason"]
    assert row["control"] != row["candidate"]


def test_tokenizer_mismatch_is_ineligible_and_ce_cannot_cross_tokenizers() -> None:
    record = _mixture({"tokenizer_identity": {"fingerprint": h("other-tokenizer")}})
    candidate = _candidate(record)
    assert "tokenizer_identity" in _violations(candidate)
    assert any(
        r.startswith("cross_tokenizer_primary_metric") for r in candidate["ineligible_reasons"]
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"lr_policy": "legacy_base_then_postcommit_v1"}, "lr_policy"),
        ({"precision": "fp32"}, "precision"),
        ({"budget_targets": 1_200_000_000, "committed_targets": 1_200_000_000}, "budget_targets"),
        (
            {
                "schedule": {
                    "config": {
                        "type": "warmup_cosine",
                        "warmup_valid_targets": 20_000_000,
                        "horizon_valid_targets": 1_000_000_000,
                        "min_lr_ratio": 0.1,
                    },
                    "component": {"key": "warmup_cosine"},
                }
            },
            "schedule.warmup_valid_targets",
        ),
    ],
)
def test_manifest_frozen_values_contradicted_by_evidence_are_ineligible(
    overrides: dict[str, Any], field: str
) -> None:
    candidate = _candidate(_mixture(overrides))
    assert not candidate["eligible"]
    frozen = {
        v["field"]
        for v in candidate["field_violations"]
        if v["classification"] == "MANIFEST_FROZEN"
    }
    assert field in frozen


def test_incomplete_evaluation_is_incomplete_never_a_score() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    broken = make_evidence(
        "run-m1-C3",
        C_TUPLES[3][1:],
        primary=0.0,
        budget=manifest["training_budget_targets"],
        components=M1_COMPONENTS,
        endpoint_tier="endpoint_confirmation",
        endpoint_complete=False,
    )
    runs = [r for r in runs if r.label != "m1-C3"] + [run("m1-C3", "m1", broken)]
    candidate = _candidate(compare_science(manifest, runs))
    assert candidate["eligible"]
    assert candidate["pairing"]["n_complete"] == 4
    pair = next(p for p in candidate["pairing"]["pairs"] if p["tuple_id"] == "C3")
    assert pair["state"] == "INCOMPLETE"
    assert candidate["decision"]["result"] == "INCOMPLETE"
    assert all(p["pair_id"] != "C3" for p in candidate["statistics"]["pairs"])
    assert candidate["promotion"]["state"] == "PROVISIONAL"


def test_failed_run_remains_visible_and_blocks_confirmation() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, {k: v for k, v in CAND.items() if k != "C4"})
    runs.append(run("m1-C4-oom", "m1", None, status="failed", failure="OOM at C=512M (SYNTHETIC)"))
    record = compare_science(manifest, runs)
    failed = next(r for r in record["runs"] if r["label"] == "m1-C4-oom")
    assert failed["state"] == "failed" and "OOM" in failed["reasons"][0]
    candidate = _candidate(record)
    assert candidate["pairing"]["n_complete"] == 4
    assert candidate["promotion"]["state"] == "PROVISIONAL"
    assert record["summary"]["failed_or_excluded_runs"] == 1


def test_pairs_use_explicit_tuples_and_input_order_is_irrelevant() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    reference = compare_science(manifest, runs)
    assert [p["pair_id"] for p in _candidate(reference)["statistics"]["pairs"]] == [
        "C0",
        "C1",
        "C2",
        "C3",
        "C4",
    ]
    rng = random.Random(7)
    for _ in range(3):
        shuffled = list(runs)
        rng.shuffle(shuffled)
        again = compare_science(manifest, shuffled)
        assert again["candidates"] == reference["candidates"]


def test_missing_pair_is_incomplete() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, {k: v for k, v in CAND.items() if k != "C4"})
    candidate = _candidate(compare_science(manifest, runs))
    pair = next(p for p in candidate["pairing"]["pairs"] if p["tuple_id"] == "C4")
    assert pair["state"] == "MISSING" and pair["missing"] == ["candidate"]
    assert candidate["decision"]["result"] == "INCOMPLETE"


def test_duplicate_pair_is_refused() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    rerun = make_evidence(
        "run-m1-C0-rerun",
        C_TUPLES[0][1:],
        primary=2.90,
        budget=manifest["training_budget_targets"],
        components=M1_COMPONENTS,
        endpoint_tier="endpoint_confirmation",
    )
    runs.append(run("m1-C0-rerun", "m1", rerun))
    candidate = _candidate(compare_science(manifest, runs))
    assert not candidate["eligible"]
    assert candidate["pairing"]["pairs"][0]["state"] == "DUPLICATE"
    assert any("repeats, not replicates" in r for r in candidate["ineligible_reasons"])


def test_same_evidence_listed_twice_is_refused() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    runs.append(run("m1-C0-again", "m1", runs[1].evidence))
    record = compare_science(manifest, runs)
    again = next(r for r in record["runs"] if r["label"] == "m1-C0-again")
    assert again["state"] == "invalid" and "listed twice" in again["reasons"][-1]
    assert not _candidate(record)["eligible"]


def test_mismatched_seeds_are_not_paired() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, {k: v for k, v in CAND.items() if k != "C0"})
    wrong = make_evidence(
        "run-m1-C0-wrong",
        (401, 20002, 20261001),
        primary=2.9,
        budget=manifest["training_budget_targets"],
        components=M1_COMPONENTS,
        endpoint_tier="endpoint_confirmation",
    )
    runs.append(run("m1-C0-wrong-training-seed", "m1", wrong))
    record = compare_science(manifest, runs)
    candidate = _candidate(record)
    assert not candidate["eligible"]
    assert any("not in the frozen roster" in r for r in candidate["ineligible_reasons"])
    pair = next(p for p in candidate["pairing"]["pairs"] if p["tuple_id"] == "C0")
    assert pair["state"] == "MISSING"


def test_five_reruns_of_one_seed_cannot_satisfy_a_five_pair_confirmation() -> None:
    manifest = superiority_manifest()
    seeds = C_TUPLES[0][1:]
    runs = []
    for i in range(5):
        for arm, components, value in (
            ("m0", None, 3.0 + i * 0.01),
            ("m1", M1_COMPONENTS, 2.9 + i * 0.01),
        ):
            evidence = make_evidence(
                f"run-{arm}-rerun{i}",
                seeds,
                primary=value,
                budget=manifest["training_budget_targets"],
                components=components,
                endpoint_tier="endpoint_confirmation",
            )
            runs.append(run(f"{arm}-rerun{i}", arm, evidence))
    candidate = _candidate(compare_science(manifest, runs))
    assert candidate["pairing"]["n_complete"] <= 1
    assert not candidate["eligible"]
    assert candidate["promotion"]["state"] == "NOT_ELIGIBLE"
    assert candidate["statistics"] is None
    # Relabelling the same seeds as five roster tuples is refused at the manifest.
    relabelled = copy.deepcopy(manifest)
    for i, entry in enumerate(relabelled["replicate_roster"]):
        entry.update(
            init_seed=seeds[0], training_seed=seeds[1], data_seed=seeds[2], tuple_id=f"C{i}"
        )
    record = compare_science(relabelled, runs)
    assert not record["manifest_validation"]["valid"]
    assert record["summary"]["overall"] == "INELIGIBLE"


def test_microbatch_grouping_difference_is_allowed_without_parameter_hash_equality() -> None:
    manifest = microbatch_manifest()
    values = {
        "b8": SCREEN,
        "b16": {k: v + 0.001 for k, v in SCREEN.items()},
        "b32": {k: v + 0.002 for k, v in SCREEN.items()},
    }
    record = compare_science(manifest, microbatch_runs(manifest, values))
    for arm in ("b16", "b32"):
        candidate = _candidate(record, arm)
        assert candidate["eligible"], candidate["ineligible_reasons"]
        diff = {d["field"]: d for d in candidate["pairing"]["pairs"][0]["field_diff"]}
        assert diff["microbatch_sequences"]["status"] == "declared_difference"
        assert diff["final_model_state_digest"]["status"] == "recorded_difference"
        assert diff["final_model_state_digest"]["classification"] == "RECORDED_MAY_DIFFER"
        for fixed in (
            "data_trace_digest",
            "update_boundaries_digest",
            "per_source_exposure",
            "global_batch_valid_targets",
            "initial_model_state_digest",
        ):
            assert (
                diff[fixed]["status"] == "match" and diff[fixed]["classification"] == "MUST_MATCH"
            )
        # A screen never selects a microbatch winner: it ranks, it does not confirm.
        assert candidate["promotion"]["state"] == "SCREEN_ONLY"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"data_trace_digest": h("different-examples-or-order")}, "data_trace_digest"),
        ({"per_source_exposure": {"prose": {"valid_targets": 1}}}, "per_source_exposure"),
        ({"update_boundaries_digest": h("different-updates")}, "update_boundaries_digest"),
        ({"global_batch_valid_targets": 131_072}, "global_batch_valid_targets"),
        ({"initial_model_state_digest": h("other-init")}, "initial_model_state_digest"),
    ],
)
def test_microbatch_track_requires_identical_data_exposure(
    overrides: dict[str, Any], field: str
) -> None:
    manifest = microbatch_manifest()
    values = {"b8": SCREEN, "b16": SCREEN, "b32": SCREEN}
    record = compare_science(
        manifest, microbatch_runs(manifest, values, overrides={"b16": overrides})
    )
    b16, b32 = _candidate(record, "b16"), _candidate(record, "b32")
    assert not b16["eligible"] and field in _violations(b16)
    assert b32["eligible"]


def test_mixture_consequences_need_their_cause() -> None:
    manifest = superiority_manifest()
    # Candidate claims M1 weights but its evidence carries the control's components.
    runs = mixture_pair_runs(
        manifest,
        CTRL,
        CAND,
        candidate_overrides={
            "mixture_components": copy.deepcopy(
                superiority_manifest()["control_arm"]["intervention"]["mixture_components"]
            )
        },
    )
    candidate = _candidate(compare_science(manifest, runs))
    assert not candidate["eligible"]
    frozen = {v["field"] for v in candidate["field_violations"]}
    assert "mixture_components" in frozen


def test_run_for_an_undeclared_arm_is_refused() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    stray = make_evidence(
        "run-m9",
        C_TUPLES[0][1:],
        primary=2.0,
        budget=manifest["training_budget_targets"],
        endpoint_tier="endpoint_confirmation",
    )
    runs.append(run("m9-C0", "m9", stray))
    record = compare_science(manifest, runs)
    assert not _candidate(record)["eligible"]


def test_altered_evidence_or_record_is_refused() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, CAND)
    tampered = copy.deepcopy(runs[1].evidence)
    assert tampered is not None
    tampered["evaluations"]["endpoint_confirmation@1000000000"]["metrics"][
        "equal_domain_text_ce_nats_per_token"
    ] = 1.0
    runs[1] = run(runs[1].label, runs[1].arm_id, tampered)
    record = compare_science(manifest, runs)
    row = next(r for r in record["runs"] if r["label"] == runs[1].label)
    assert row["state"] == "invalid" and "does not verify" in row["reasons"][0]
    good = _mixture()
    good["candidates"][0]["promotion"]["state"] = "PROMOTE_TO_150M"
    with pytest.raises(ComparisonError, match="altered"):
        verify_comparison_record(good)


def test_unknown_field_value_fails_closed() -> None:
    candidate = _candidate(_mixture({"model_parameter_count": None}))
    assert not candidate["eligible"]
    row = next(v for v in candidate["field_violations"] if v["field"] == "model_parameter_count")
    assert row["status"] == "UNKNOWN"


def test_exploratory_screen_tuples_pair_by_roster() -> None:
    manifest = superiority_manifest(stage="screen", tuples=E_TUPLES[:1])
    runs = mixture_pair_runs(manifest, {"E0": 3.4}, {"E0": 3.35})
    candidate = _candidate(compare_science(manifest, runs))
    assert candidate["eligible"]
    assert candidate["statistics"]["n_pairs"] == 1
    assert candidate["statistics"]["ci_raw_delta"] is None


def test_cross_tokenizer_ce_guard_holds_even_when_a_track_allows_the_tokenizer_to_vary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test-only certified track lets the tokenizer vary; CE still cannot pick a winner."""
    from xlm.comparison import science_tracks
    from xlm.comparison.science_tracks import ScienceTrack

    probe = ScienceTrack(
        track_id="tokenizer_probe_test_only",
        certified=True,
        varied=frozenset({"tokenizer_identity"}),
        description="TEST ONLY: isolates the cross-tokenizer metric guard.",
        consequences={},
    )
    monkeypatch.setitem(science_tracks.SCIENCE_TRACKS, probe.track_id, probe)
    other = {"fingerprint": h("candidate-tokenizer")}

    def manifest_for(metric: str) -> dict[str, Any]:
        doc = superiority_manifest()
        doc["track"] = probe.track_id
        doc["required_invariants"] = probe.must_match()
        doc["intended_differences"] = ["tokenizer_identity"]
        doc["control_arm"]["intervention"] = {
            "tokenizer_identity": {"fingerprint": h("tokenizer-bpe-32768")}
        }
        doc["candidate_arms"][0]["intervention"] = {"tokenizer_identity": other}
        doc["primary_metric"] = {"name": metric, "direction": "lower_is_better"}
        doc["secondary_metrics"] = []
        doc["multiplicity"]["guardrail_family_size"] = 0
        return doc

    for metric, expect_eligible in (
        ("equal_domain_text_ce_nats_per_token", False),
        ("equal_domain_text_bpb", True),
    ):
        manifest = manifest_for(metric)
        runs = []
        for entry in manifest["replicate_roster"]:
            t = entry["tuple_id"]
            seeds = (entry["init_seed"], entry["training_seed"], entry["data_seed"])
            for arm, value, overrides in (
                ("m0", CTRL[t], None),
                ("m1", CAND[t], {"tokenizer_identity": other}),
            ):
                evidence = make_evidence(
                    f"run-{arm}-{t}",
                    seeds,
                    primary=value,
                    budget=manifest["training_budget_targets"],
                    endpoint_tier="endpoint_confirmation",
                    overrides=overrides,
                )
                runs.append(run(f"{arm}-{t}", arm, evidence))
        candidate = _candidate(compare_science(manifest, runs))
        assert candidate["eligible"] is expect_eligible, candidate["ineligible_reasons"]
        if not expect_eligible:
            assert any(
                r.startswith("cross_tokenizer_primary_metric")
                for r in candidate["ineligible_reasons"]
            )
            assert candidate["statistics"] is None
            assert candidate["decision"]["result"] == "INELIGIBLE"
