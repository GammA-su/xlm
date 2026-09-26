"""P35 M4: science-v1 comparison manifest schema, validation and hashing (SYNTHETIC)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from p35_m4_support import C_TUPLES, microbatch_manifest, superiority_manifest
from xlm.comparison.science_manifest import (
    DRAFT,
    REQUIRED_KEYS,
    manifest_hash,
    validate_manifest,
)
from xlm.comparison.science_tracks import (
    MICROBATCH_TRACK,
    MIXTURE_TRACK,
    SCIENCE_TRACKS,
    SCIENTIFIC_FIELDS,
    FieldClass,
)

DRAFTS = Path(__file__).resolve().parents[1] / "recipes" / "science_comparisons"


def _problems(doc: Any) -> list[str]:
    return [f"{p.field}: {p.problem}" for p in validate_manifest(doc).problems]


def test_reference_manifests_are_valid_and_hash_canonically() -> None:
    for doc in (
        microbatch_manifest(),
        superiority_manifest(),
        microbatch_manifest(stage="confirmation"),
    ):
        validation = validate_manifest(doc)
        assert validation.valid, _problems(doc)
        assert validation.manifest_hash == manifest_hash(doc)
        reordered = json.loads(json.dumps(doc, sort_keys=False))
        reordered = dict(reversed(list(reordered.items())))
        assert manifest_hash(reordered) == manifest_hash(doc)


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_every_field_is_required_never_defaulted(key: str) -> None:
    doc = superiority_manifest()
    del doc[key]
    assert f"{key}: missing required field (unknown is never defaulted)" in _problems(doc)


def test_unknown_field_and_results_are_refused() -> None:
    doc = superiority_manifest()
    doc["results"] = {"winner": "m1"}
    assert any(p.startswith("results: unknown field") for p in _problems(doc))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("practical_margin", 0.02),
        ("replicate_roster", "drop-one"),
        ("primary_metric", {"name": "equal_domain_text_bpb", "direction": "lower_is_better"}),
    ],
)
def test_changing_frozen_inputs_changes_the_manifest_hash(key: str, value: Any) -> None:
    doc = superiority_manifest()
    before = manifest_hash(doc)
    if value == "drop-one":
        doc[key] = doc[key][:-1]
    else:
        doc[key] = value
    assert manifest_hash(doc) != before


def test_family_size_is_frozen_input_and_cannot_undercount_candidates() -> None:
    doc = microbatch_manifest()
    before = manifest_hash(doc)
    doc["multiplicity"]["family_size"] = 1
    assert manifest_hash(doc) != before
    assert any("smaller than the 2 candidate arms" in p for p in _problems(doc))
    missing = microbatch_manifest()
    missing["multiplicity"] = {"family_id": "x", "family_size": 2}
    assert any(p.startswith("multiplicity:") for p in _problems(missing))


def test_metric_direction_is_verified_not_trusted() -> None:
    doc = superiority_manifest()
    doc["primary_metric"]["direction"] = "higher_is_better"
    assert any("would invert it" in p for p in _problems(doc))
    unknown = superiority_manifest()
    unknown["secondary_metrics"][0]["name"] = "mystery_score"
    assert any("no verified direction" in p for p in _problems(unknown))


def test_benchmark_cannot_be_the_primary_metric() -> None:
    doc = superiority_manifest()
    doc["primary_metric"] = {"name": "blimp_acc", "direction": "higher_is_better"}
    assert any("frozen held-out LM quality" in p for p in _problems(doc))


def test_intended_differences_must_be_track_allowlisted_and_caused() -> None:
    doc = microbatch_manifest()
    doc["intended_differences"] = ["microbatch_sequences", "tokenizer_identity"]
    assert any("tokenizer_identity' is MUST_MATCH" in p for p in _problems(doc))
    mixture = superiority_manifest()
    mixture["intended_differences"] = ["data_trace_digest"]
    mixture["control_arm"]["intervention"] = {}
    mixture["candidate_arms"][0]["intervention"] = {}
    assert any("may only be declared together with" in p for p in _problems(mixture))


def test_required_invariants_must_cover_track_must_match() -> None:
    doc = microbatch_manifest()
    doc["required_invariants"] = [
        f for f in doc["required_invariants"] if f != "tokenizer_identity"
    ]
    assert any("omits track MUST_MATCH fields ['tokenizer_identity']" in p for p in _problems(doc))


def test_declared_intervention_must_be_realized_by_the_arms() -> None:
    doc = microbatch_manifest()
    doc["candidate_arms"][0]["intervention"] = {"microbatch_sequences": 8}
    assert any("not realized" in p for p in _problems(doc))


@pytest.mark.parametrize(("scale", "pairs"), [("50m", 5), ("150m", 3), ("300m", 3)])
def test_confirmation_sample_sizes_are_fixed(scale: str, pairs: int) -> None:
    doc = superiority_manifest(scale=scale)
    if scale != "50m":
        doc["scale_promotion"]["prerequisite"] = {
            "manifest_hash": "a" * 64,
            "required_state": {"150m": "PROMOTE_TO_150M", "300m": "PROMOTE_TO_300M"}[scale],
        }
    assert validate_manifest(doc).valid, _problems(doc)
    fewer = copy.deepcopy(doc)
    fewer["confirmatory_pairs"] = pairs - 1
    fewer["replicate_roster"] = fewer["replicate_roster"][:-1]
    problems = _problems(fewer)
    assert any(f"requires exactly {pairs} fresh pairs" in p for p in problems)


def test_confirmation_requires_fresh_tuples_full_budget_and_confirmation_endpoint() -> None:
    doc = superiority_manifest()
    doc["replicate_roster"][0]["role"] = "exploratory"
    doc["training_budget_targets"] = 128_000_000
    doc["primary_endpoint"] = {"tier": "full_lm", "planned_threshold": 128_000_000}
    problems = _problems(doc)
    assert any("fresh 'confirmation' tuples only" in p for p in problems)
    assert any("full-budget" in p for p in problems)
    assert any("confirmation LM endpoint" in p for p in problems)


def test_larger_scale_confirmation_needs_a_prior_scale_prerequisite() -> None:
    doc = superiority_manifest(scale="150m")
    assert any("prerequisite" in p for p in _problems(doc))


def test_roster_refuses_same_seed_tuple_twice() -> None:
    doc = superiority_manifest()
    clone = dict(doc["replicate_roster"][0])
    clone["tuple_id"] = "C0-rerun"
    doc["replicate_roster"][1] = clone
    assert any("same-seed rerun is not a new replicate" in p for p in _problems(doc))


def test_endpoint_must_be_the_exact_budget() -> None:
    doc = superiority_manifest(stage="screen")
    doc["primary_endpoint"]["planned_threshold"] = 96_000_000
    assert any("exact declared budget" in p for p in _problems(doc))


def test_ci_level_and_rule_versions_are_fixed() -> None:
    doc = superiority_manifest()
    doc["ci_level"] = 0.9
    doc["stopping_rule"] = "stop_when_significant"
    problems = _problems(doc)
    assert any(p.startswith("ci_level") for p in problems)
    assert any(p.startswith("stopping_rule") for p in problems)


def test_missing_margin_is_a_promotion_blocker_not_a_default() -> None:
    doc = superiority_manifest(margin=None)
    validation = validate_manifest(doc)
    assert validation.valid
    assert [b.field for b in validation.promotion_blockers] == ["practical_margin"]


def test_scale_promotion_requires_order_robustness() -> None:
    doc = superiority_manifest()
    doc["scale_promotion"]["intent"] = True
    assert any("order_robustness.required" in p for p in _problems(doc))


def test_track_tables_classify_every_field() -> None:
    for track in SCIENCE_TRACKS.values():
        table = track.table()
        assert set(table) == set(SCIENTIFIC_FIELDS)
    assert MICROBATCH_TRACK.classify("microbatch_sequences") is FieldClass.INTENTIONALLY_VARIED
    assert MICROBATCH_TRACK.classify("final_model_state_digest") is FieldClass.RECORDED_MAY_DIFFER
    for fixed in (
        "data_trace_digest",
        "global_batch_valid_targets",
        "initial_model_state_digest",
        "tokenizer_identity",
        "update_boundaries_digest",
        "per_source_exposure",
    ):
        assert MICROBATCH_TRACK.classify(fixed) is FieldClass.MUST_MATCH
    for varied in ("mixture_components", "data_trace_digest", "per_source_exposure"):
        assert MIXTURE_TRACK.classify(varied) is FieldClass.INTENTIONALLY_VARIED
    for fixed in (
        "tokenizer_identity",
        "model_architecture",
        "within_source_order_policy",
        "optimizer",
        "schedule",
        "update_boundaries_digest",
        "evaluator_identities",
    ):
        assert MIXTURE_TRACK.classify(fixed) is FieldClass.MUST_MATCH


def test_future_tracks_are_declared_but_uncertified() -> None:
    doc = superiority_manifest()
    doc["track"] = "tokenizer_v0_uncertified"
    assert any("no certified eligibility semantics" in p for p in _problems(doc))


def test_checked_in_drafts_are_nonexecutable_without_results() -> None:
    files = sorted(DRAFTS.glob("*.yaml"))
    assert {f.name for f in files} == {
        "draft_science_v1_microbatch_b8_b16_b32.yaml",
        "draft_science_v1_mixture_m0_m1.yaml",
    }
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["status"] == DRAFT
        problems = validate_manifest(doc).problems
        # The draft is nonexecutable; apart from its status, only its margins and
        # rationale are consciously left for the user to freeze.
        fields = {p.field for p in problems}
        assert "status" in fields
        assert fields <= {"status"}, problems
        assert not any(k in doc for k in ("results", "outcomes", "scores"))


def test_confirmation_roster_uses_contract_c_tuples() -> None:
    doc = superiority_manifest()
    assert [
        (e["tuple_id"], e["init_seed"], e["training_seed"], e["data_seed"])
        for e in doc["replicate_roster"]
    ] == C_TUPLES
