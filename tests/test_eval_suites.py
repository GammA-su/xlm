"""Acceptance tests for P15 tiering, firewall, evidence and references.

These run without the harness installed: they cover the policy layer that decides
what may be scored, the index arithmetic, identity-checked caching, and the
bounded comparator-download gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.evaluation.coverage import undeclared_coverage
from xlm.evaluation.evidence import (
    EVIDENCE_VERSION,
    EvaluationEvidence,
    EvidenceCache,
    ItemEvidence,
    TaskEvidence,
    items_from_harness_samples,
    task_evidence_from_items,
)
from xlm.evaluation.harness import (
    SUPPORTED_LM_EVAL_VERSION,
    HarnessIdentity,
    build_harness_identity,
    harness_version,
)
from xlm.evaluation.reference import (
    COMMON_SCORING_POLICY,
    DownloadPlanError,
    ModelDownloadPlan,
    ReferenceCheckpoint,
    ReferenceRegistry,
)
from xlm.evaluation.suites import (
    FinalAuthorization,
    FinalAuthorizationRequiredError,
    IncompleteCoverageError,
    SplitFirewallError,
    SuiteTier,
    TaskScore,
    assert_no_final_split_leak,
    build_final_request,
    compute_four_task_index,
    load_dataset_pins,
    partition_blimp_subdatasets,
    partition_grouped_items,
    resolve_suite,
)

PINS_PATH = Path(__file__).resolve().parents[1] / "manifests" / "eval_dataset_pins.yaml"


# ---------------------------------------------------------------- dataset pins


def test_official_dataset_pins_are_resolved_and_immutable() -> None:
    pins = load_dataset_pins(PINS_PATH)
    expected = {"arc_easy", "hellaswag", "piqa", "blimp"}
    assert expected <= set(pins)
    for name in expected:
        pin = pins[name]
        assert pin.resolved and pin.revision
        assert len(pin.revision) == 40, "revision must be a full commit SHA"
        assert pin.require_resolved() == pin.revision


def test_variants_carry_the_pinned_revision_into_the_task_config() -> None:
    pins = load_dataset_pins(PINS_PATH)
    variants = resolve_suite(SuiteTier.SEARCH, pins)
    arc = next(v for v in variants if v.lm_eval_task == "arc_easy")
    spec = arc.to_task_spec()
    assert spec["dataset_kwargs"]["revision"] == pins["arc_easy"].revision


# ------------------------------------------------------------- split firewall


def test_search_and_confirmation_never_reference_final_splits() -> None:
    pins = load_dataset_pins(PINS_PATH)
    search = resolve_suite(SuiteTier.SEARCH, pins)
    confirmation = resolve_suite(SuiteTier.CONFIRMATION, pins)

    search_splits = {v.lm_eval_task: v.split for v in search}
    confirmation_splits = {v.lm_eval_task: v.split for v in confirmation}

    assert search_splits["arc_easy"] == "train"
    assert confirmation_splits["arc_easy"] == "validation"
    assert search_splits["hellaswag"] == confirmation_splits["hellaswag"] == "train"
    assert search_splits["piqa"] == confirmation_splits["piqa"] == "train"


def test_final_splits_are_exactly_the_policy_split() -> None:
    pins = load_dataset_pins(PINS_PATH)
    auth = FinalAuthorization(
        operator_authorized=True, request_hash="request", authorized_by="operator", ticket="T1"
    )
    variants = resolve_suite(SuiteTier.FINAL, pins, final_authorization=auth)
    splits = {v.lm_eval_task: v.split for v in variants}
    assert splits["arc_easy"] == "test"
    assert splits["hellaswag"] == "validation"
    assert splits["piqa"] == "validation"


def test_final_requires_operator_authorization() -> None:
    pins = load_dataset_pins(PINS_PATH)
    with pytest.raises(FinalAuthorizationRequiredError, match="never resolve a final split"):
        resolve_suite(SuiteTier.FINAL, pins)
    with pytest.raises(FinalAuthorizationRequiredError):
        resolve_suite(
            SuiteTier.FINAL,
            pins,
            final_authorization=FinalAuthorization(operator_authorized=False, request_hash="x"),
        )


def test_firewall_detects_a_developer_variant_pointing_at_a_final_split() -> None:
    from xlm.evaluation.suites import TaskVariant

    leaked = TaskVariant(
        variant_id="xlm_arc_easy_search",
        lm_eval_task="arc_easy",
        tier=SuiteTier.SEARCH,
        split="test",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
    )
    with pytest.raises(SplitFirewallError, match="final split 'test'"):
        assert_no_final_split_leak([leaked], SuiteTier.SEARCH)


# --------------------------------------------------------------- partitions


def test_blimp_partition_is_whole_disjoint_subdatasets() -> None:
    names = [f"blimp_subdataset_{i:02d}" for i in range(67)]
    partition = partition_blimp_subdatasets(names)
    search = partition[SuiteTier.SEARCH]
    confirmation = partition[SuiteTier.CONFIRMATION]
    final = partition[SuiteTier.FINAL]

    assert not set(search) & set(confirmation)
    assert not set(search) & set(final)
    assert not set(confirmation) & set(final)
    assert sorted(search + confirmation + final) == sorted(names)
    assert len(search) == pytest.approx(0.2 * 67, abs=1)
    assert len(confirmation) == pytest.approx(0.2 * 67, abs=1)
    assert len(final) == pytest.approx(0.6 * 67, abs=2)


def test_blimp_partition_is_deterministic_and_seed_sensitive() -> None:
    names = [f"blimp_{i}" for i in range(30)]
    assert partition_blimp_subdatasets(names) == partition_blimp_subdatasets(names)
    rotated = partition_blimp_subdatasets(sorted(names, reverse=True))
    assert {k: sorted(v) for k, v in rotated.items()} == {
        k: sorted(v) for k, v in partition_blimp_subdatasets(names).items()
    }


def test_grouped_partition_keeps_groups_together_and_disjoint() -> None:
    items = [{"ctx": f"ctx_{i // 2}", "id": i} for i in range(40)]
    search = partition_grouped_items(items, SuiteTier.SEARCH, "ctx")
    confirmation = partition_grouped_items(items, SuiteTier.CONFIRMATION, "ctx")

    search_ctx = {i["ctx"] for i in search}
    confirmation_ctx = {i["ctx"] for i in confirmation}
    assert not search_ctx & confirmation_ctx
    assert search_ctx | confirmation_ctx == {f"ctx_{i}" for i in range(20)}
    with pytest.raises(SplitFirewallError):
        partition_grouped_items(items, SuiteTier.FINAL, "ctx")


# ------------------------------------------------------------- four-task index


def _score(task: str, value: float, chance: float, **kwargs: object) -> TaskScore:
    defaults: dict[str, object] = {
        "metric_name": "acc_norm",
        "scored_items": 100,
        "total_items": 100,
    }
    defaults.update(kwargs)
    return TaskScore(task=task, value=value, chance=chance, **defaults)  # type: ignore[arg-type]


def test_index_requires_complete_declared_coverage() -> None:
    partial = {"arc_easy": _score("arc_easy", 0.5, 0.25)}
    index = compute_four_task_index(partial)
    assert index.complete is False
    assert index.index is None
    assert index.missing == ["blimp", "hellaswag", "piqa"]


def test_index_matches_the_declared_formula_and_chance_references() -> None:
    scores = {
        "blimp": _score("blimp", 0.8, 0.5),
        "arc_easy": _score("arc_easy", 0.5, 0.25),
        "hellaswag": _score("hellaswag", 0.4, 0.25),
        "piqa": _score("piqa", 0.6, 0.5),
    }
    index = compute_four_task_index(scores)
    assert index.complete is True
    expected = (
        100.0
        * ((0.8 - 0.5) / 0.5 + (0.5 - 0.25) / 0.75 + (0.4 - 0.25) / 0.75 + (0.6 - 0.5) / 0.5)
        / 4
    )
    assert index.index == pytest.approx(expected)
    assert index.chance_references == {
        "blimp": 0.5,
        "arc_easy": 0.25,
        "hellaswag": 0.25,
        "piqa": 0.5,
    }


def test_blimp_is_macro_averaged_before_weighting() -> None:
    scores = {
        "blimp": _score(
            "blimp",
            0.99,
            0.5,
            subdataset_scores={"blimp_a": 0.2, "blimp_b": 0.4},
        ),
        "arc_easy": _score("arc_easy", 0.5, 0.25),
        "hellaswag": _score("hellaswag", 0.4, 0.25),
        "piqa": _score("piqa", 0.6, 0.5),
    }
    index = compute_four_task_index(scores)
    # BLiMP contributes the 0.3 subdataset macro, not the misleading 0.99 aggregate.
    expected_blimp = (0.3 - 0.5) / 0.5
    assert index.components["blimp"] == pytest.approx(expected_blimp)


def test_negative_components_are_not_clipped() -> None:
    scores = {
        "blimp": _score("blimp", 0.2, 0.5),
        "arc_easy": _score("arc_easy", 0.1, 0.25),
        "hellaswag": _score("hellaswag", 0.1, 0.25),
        "piqa": _score("piqa", 0.2, 0.5),
    }
    index = compute_four_task_index(scores)
    assert index.complete and index.index is not None and index.index < 0


def test_zero_scored_items_withhold_the_index() -> None:
    scores = {
        "blimp": _score("blimp", 0.8, 0.5),
        "arc_easy": _score("arc_easy", 0.0, 0.25, scored_items=0),
        "hellaswag": _score("hellaswag", 0.4, 0.25),
        "piqa": _score("piqa", 0.6, 0.5),
    }
    index = compute_four_task_index(scores)
    assert index.complete is False
    assert index.missing == ["arc_easy:no_scored_items"]


# --------------------------------------------------------- final request gate


def test_final_request_is_frozen_and_hashable() -> None:
    pins = load_dataset_pins(PINS_PATH)
    request = build_final_request("ckpt_hash", "tok_hash", pins, limit=None)
    assert request["kind"] == "final_evaluation_request"
    assert request["request_hash"]
    again = build_final_request("ckpt_hash", "tok_hash", pins, limit=None)
    assert again["request_hash"] == request["request_hash"]
    changed = build_final_request("other_ckpt", "tok_hash", pins, limit=None)
    assert changed["request_hash"] != request["request_hash"]


# ----------------------------------------------------------- evidence & cache


def _item(item_id: str, correct: bool, correct_norm: bool) -> ItemEvidence:
    return ItemEvidence(
        task="arc_easy",
        item_id=item_id,
        gold=0,
        predicted=0 if correct else 1,
        predicted_normalized=0 if correct_norm else 1,
        choice_log_likelihoods=[1.0, 0.5],
        choice_normalized_scores=[1.0, 0.6],
        raw_margin=0.5,
        normalized_margin=0.4,
        is_correct=correct,
        is_correct_normalized=correct_norm,
    )


def test_task_evidence_counts_omissions_and_truncation() -> None:
    items = [_item("i0", True, True), _item("i1", False, True)]
    omitted = _item("i2", True, True)
    omitted = ItemEvidence(**{**omitted.__dict__, "omitted_reason": "limit"})
    evidence = task_evidence_from_items(
        "arc_easy", "xlm_arc_easy_search", "acc_norm", 0.25, [*items, omitted], total_items=3
    )
    assert evidence.scored_items == 2
    assert evidence.total_items == 3
    assert evidence.omitted_items == 1
    assert evidence.acc == pytest.approx(0.5)
    assert evidence.acc_norm == pytest.approx(1.0)


def _evidence(fingerprint: str, value: float = 0.5) -> EvaluationEvidence:
    task = TaskEvidence(
        task="arc_easy",
        variant_id="xlm_arc_easy_search",
        metric_name="acc_norm",
        acc=value,
        acc_norm=value,
        chance=0.25,
        scored_items=1,
        total_items=1,
        items=[_item("i0", True, True)],
    )
    index = compute_four_task_index({"arc_easy": task.score()})
    return EvaluationEvidence(
        evidence_version=EVIDENCE_VERSION,
        identity_fingerprint=fingerprint,
        identity={"fingerprint": fingerprint, "precision": "fp32"},
        tasks={"arc_easy": task},
        index=index,
        limit=None,
        # Evidence written today always records its coverage state. Omitting it
        # is the legacy case, covered separately in test_eval_declared_inputs.
        coverage=undeclared_coverage(),
        raw_identity_fingerprint=fingerprint,
        aggregate_identity_fingerprint=f"agg-{fingerprint}",
        notes=[],
    )


def test_evidence_round_trips_deterministically(tmp_path: Path) -> None:
    evidence = _evidence("abc123")
    path = evidence.save(tmp_path / "e.json")
    loaded = EvaluationEvidence.load(path)
    assert loaded.to_dict() == evidence.to_dict()
    assert loaded.tasks["arc_easy"].items[0].item_id == "i0"


def test_cache_rejects_a_stale_or_foreign_identity(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path)
    evidence = _evidence("abc123")
    cache.store(evidence)
    assert cache.load_if_match("abc123", evidence.identity) is not None
    # Same fingerprint path but different identity payload: refuse.
    assert cache.load_if_match("abc123", {"fingerprint": "abc123", "precision": "bf16"}) is None
    assert cache.load_if_match("missing", evidence.identity) is None


# --------------------------------------------------------------- references


def test_reference_registry_round_trip_and_no_silent_replacement(tmp_path: Path) -> None:
    registry = ReferenceRegistry()
    entry = ReferenceCheckpoint(
        name="tiny_author_fixture",
        checkpoint_hash="ckpt_1",
        tokenizer_hash="tok_1",
        unique_parameters=2_179_392,
        context_length=512,
        precision="fp32",
        harness_version=SUPPORTED_LM_EVAL_VERSION,
        common_policy_id="policy_v1",
    )
    registry.register(entry)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(entry)
    registry.register(entry, replace=True)
    path = registry.save(tmp_path / "refs.json")
    loaded = ReferenceRegistry.load(path)
    assert loaded.entries["tiny_author_fixture"].identity() == entry.identity()
    assert loaded.common_policy == COMMON_SCORING_POLICY


def test_model_download_plan_requires_authorization_revision_and_caps() -> None:
    base: dict[str, object] = {
        "source_repository": "some/model",
        "source_revision": "a" * 40,
        "files": ("model.safetensors",),
        "max_total_bytes": 1024,
        "scratch_dir": "scratch",
    }
    with pytest.raises(DownloadPlanError, match="no explicit operator authorization"):
        ModelDownloadPlan(**base).validate()  # type: ignore[arg-type]

    authorized = {**base, "operator_authorized": True, "operator_ticket": "T-1"}
    ModelDownloadPlan(**authorized).validate()  # type: ignore[arg-type]

    unbounded = {**authorized, "max_total_bytes": 0}
    with pytest.raises(DownloadPlanError, match="byte cap"):
        ModelDownloadPlan(**unbounded).validate()  # type: ignore[arg-type]

    no_revision = {**authorized, "source_revision": ""}
    with pytest.raises(DownloadPlanError, match="immutable revision"):
        ModelDownloadPlan(**no_revision).validate()  # type: ignore[arg-type]

    untrusted = {**authorized, "allowlisted_hosts": ("evil.example",)}
    with pytest.raises(DownloadPlanError, match="not on the XLM allowlist"):
        ModelDownloadPlan(**untrusted).validate()  # type: ignore[arg-type]


# ------------------------------------------------------- harness identity/pin


def test_harness_identity_changes_with_every_component() -> None:
    pins = load_dataset_pins(PINS_PATH)
    variants = resolve_suite(SuiteTier.SEARCH, pins)
    base = build_harness_identity("ckpt", "tok", variants, precision="fp32", limit=None)
    assert base.harness_version == SUPPORTED_LM_EVAL_VERSION
    assert (
        base.fingerprint()
        == build_harness_identity(
            "ckpt", "tok", variants, precision="fp32", limit=None
        ).fingerprint()
    )
    assert base.fingerprint() != build_harness_identity("other", "tok", variants).fingerprint()
    assert (
        base.fingerprint()
        != build_harness_identity(
            "ckpt", "tok", variants, precision="bf16_fp32_master"
        ).fingerprint()
    )
    assert (
        base.fingerprint()
        != build_harness_identity("ckpt", "tok", variants, limit=10).fingerprint()
    )


@pytest.mark.optional_dependency
def test_installed_harness_version_matches_the_pin_when_present() -> None:
    installed = harness_version()
    if installed is None:
        pytest.skip("eval extra not installed in this environment")
    assert installed == SUPPORTED_LM_EVAL_VERSION


def test_harness_identity_to_dict_carries_the_fingerprint() -> None:
    identity = HarnessIdentity(
        harness_version="0.4.13",
        contract_version="1",
        checkpoint_hash="c",
        tokenizer_hash="t",
        task_variant_ids=("xlm_arc_easy_search",),
        dataset_revisions=("abc",),
        split_ids=("train",),
        prompt_template_version="native",
        metric_normalization_policy="character_length",
        context_truncation_policy="rolling",
        precision="fp32",
    )
    assert identity.to_dict()["fingerprint"] == identity.fingerprint()


# ----------------------------------------------------- sample -> item evidence


def test_harness_samples_convert_with_lowest_index_tie_break() -> None:
    samples = [
        {
            "id": 0,
            "doc_id": 0,
            "target": 1,
            "resps": [[1.0, False], [1.0, False], [0.5, False]],
            "doc": {"choices": ["aa", "bb", "cccc"]},
        }
    ]
    items = items_from_harness_samples("arc_easy", samples)
    assert items[0].predicted == 0, "raw tie must pick the lowest index"
    # Normalized: 1.0/2, 1.0/2, 0.5/4 -> tie between 0 and 1, lowest index wins.
    assert items[0].predicted_normalized == 0
    assert items[0].is_correct_normalized is False


def test_incomplete_coverage_error_is_available_for_callers() -> None:
    assert issubclass(IncompleteCoverageError, RuntimeError)


def test_reference_registration_measures_parameters_locally() -> None:
    """A comparator registration counts parameters itself; card numbers are not copied."""
    pytest.importorskip("torch")
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.evaluation.reference import register_reference_from_model
    from xlm.models.transformer import TransformerBaseline

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        intermediate_size=128,
        context_length=128,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=1)
    entry = register_reference_from_model(
        name="tiny_author_fixture",
        model=model,
        tokenizer_hash="tok_fp",
        checkpoint_hash="ckpt_fp",
        harness_version=SUPPORTED_LM_EVAL_VERSION,
    )
    assert entry.unique_parameters == model.count_parameters().unique_deployed
    assert entry.unique_parameters > 0
    assert entry.source_revision is None


def test_cli_final_request_only_writes_a_gated_request(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    # A request binds actual bytes, never an empty directory's display name.
    (checkpoint / "model.pt").write_bytes(b"authored request identity fixture, not loaded")
    (checkpoint / "model_config.json").write_text('{"vocab_size": 260}', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evaluate",
            str(checkpoint),
            "--suite",
            "final",
            "--request-only",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Final evaluation request written" in result.output
    requests = list((tmp_path / "out").glob("final_request_*.json"))
    assert len(requests) == 1


def test_cli_final_without_authorization_refuses(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    result = CliRunner().invoke(app, ["evaluate", str(checkpoint), "--suite", "final"])
    assert result.exit_code == 1
    assert "never resolve a final split" in result.output


def test_cli_unknown_suite_refuses(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    result = CliRunner().invoke(app, ["evaluate", str(checkpoint), "--suite", "official"])
    assert result.exit_code == 1
    assert "unknown suite" in result.output
