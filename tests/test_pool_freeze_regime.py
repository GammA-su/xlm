"""Acceptance tests for frozen pools, tokenizer-fit sampling and the research regime.

Covers the P11 acceptance list: changing a mixture reuses the pool and tokenizer;
changing a normalization rule or source revision invalidates downstream inputs; no
train/validation membership leak; insufficient distinct text is visible; and the
artifact verifies offline.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.pools import (
    PRODUCTION_MIN_TRAIN_BYTES,
    CampaignFreeze,
    DiagnosticCorpusFreeze,
    FrozenPoolManifest,
    LeakDetectedError,
    NonTrainingFitError,
    PoolBinding,
    PoolBuildConfig,
    PoolPublicationError,
    PoolTier,
    ResearchRegimeManifest,
    SourceView,
    TokenizerFitConfig,
    ViewSelector,
    assess_sufficiency,
    build_pool,
    build_regime,
    build_tokenizer_fit_manifest,
    derive_tier,
    detect_split_leaks,
    freeze_campaign,
    regime_accepts_mixture_change,
    restrict_membership_to_split,
    tokenizer_fit_resource_plan,
    verify_pool_offline,
)
from xlm.data.pools.regime import RegimeValidationError

PROSE = [
    "Tidal patterns along the northern coast shift with lunar declination each fortnight.",
    "Medieval guild records list apprenticeship terms in unusually precise written detail.",
    "Volcanic ash layers provide chronological markers across widely separated basins.",
    "Bee colonies regulate hive temperature by coordinated and sustained wing fanning.",
    "Harbour dredging schedules balance silt accumulation against commercial shipping demand.",
    "Alpine glaciers retreat at rates that vary sharply with slope aspect and debris cover.",
]


def make_doc(
    doc_id: str,
    text: str,
    source_id: str = "fixture_src",
    split: str = "train",
    kind: str = "prose",
    metadata: dict[str, Any] | None = None,
    parent_ids: list[str] | None = None,
    cluster_ids: dict[str, str] | None = None,
) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision="rev_1",
        source_file="shard.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind=kind,
        source_metadata=metadata or {},
        parent_ids=parent_ids or [],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids=cluster_ids or {},
        split=split,
    )


def corpus() -> list[CanonicalDocument]:
    docs = [make_doc(f"train_{i}", f"{PROSE[i]} Training record {i}.") for i in range(len(PROSE))]
    docs += [
        make_doc(f"code_{i}", f"def f{i}(x):\n    return x + {i}\n", kind="code") for i in range(3)
    ]
    docs += [
        make_doc(f"val_{i}", f"{PROSE[i]} Held out for diagnostics.", split="diagnostic_val")
        for i in range(2)
    ]
    docs.append(make_doc("audit_0", f"{PROSE[0]} Reserved for text audit.", split="audit"))
    return docs


def views() -> list[SourceView]:
    return [
        SourceView(
            view_id="prose_view",
            family_id="fixture_family",
            selector=ViewSelector(document_kinds=["prose"]),
            declared_raw_byte_share=0.6,
            priority=10,
        ),
        SourceView(
            view_id="code_view",
            family_id="fixture_family",
            selector=ViewSelector(document_kinds=["code"]),
            declared_raw_byte_share=0.4,
            priority=5,
        ),
    ]


def binding(**overrides: Any) -> PoolBinding:
    base = {
        "source_revisions": {"fixture_src": "rev_1"},
        "selected_raw_files": {"fixture_src": ["shard.jsonl"]},
        "row_locator_digest": "rows_v1",
        "cleaning_policy_identity": "clean_v1",
        "dedup_policy_identity": "dedup_v1",
        "exclusion_policy_identity": "excl_v1",
        "split_policy_identity": "split_v1",
        "license_receipts": {"fixture_src": "cc-by-4.0"},
        "producer_code_version": "p11_v1",
    }
    base.update(overrides)
    return PoolBinding(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- pool


def test_pool_build_binds_every_required_component() -> None:
    assembly = build_pool(corpus(), views(), binding())
    bound = assembly.manifest.binding

    assert bound.missing_components() == []
    assert bound.source_revisions == {"fixture_src": "rev_1"}
    assert set(bound.view_identities) == {"prose_view", "code_view"}
    assert bound.overlap_policy == "exclusive_assignment"
    assert bound.license_receipts


@pytest.mark.parametrize(
    "missing_field",
    [
        "source_revisions",
        "cleaning_policy_identity",
        "dedup_policy_identity",
        "split_policy_identity",
        "license_receipts",
        "producer_code_version",
    ],
)
def test_missing_required_component_blocks_publication(missing_field: str) -> None:
    """An unbound pool cannot be told apart later from one built under other rules."""
    empty: Any = {} if missing_field in ("source_revisions", "license_receipts") else ""
    with pytest.raises(PoolPublicationError, match="missing or empty"):
        build_pool(corpus(), views(), binding(**{missing_field: empty}))


def test_pool_with_no_matching_documents_is_refused() -> None:
    narrow = [
        SourceView(
            view_id="nothing",
            family_id="fixture_family",
            selector=ViewSelector(languages=["xx"]),
        )
    ]
    with pytest.raises(PoolPublicationError, match="no document matched"):
        build_pool(corpus(), narrow, binding())


def test_pool_verifies_offline_against_its_documents() -> None:
    """Verification needs only the manifest and the stored records."""
    assembly = build_pool(corpus(), views(), binding())
    assert verify_pool_offline(assembly.manifest, assembly.accepted_documents) == []


def test_offline_verification_detects_changed_text_under_stable_ids() -> None:
    """Membership alone is not enough; the content digest catches edited text."""
    assembly = build_pool(corpus(), views(), binding())
    tampered = list(assembly.accepted_documents)
    original = tampered[0]
    altered_text = original.text + " An extra sentence nobody recorded."
    tampered[0] = replace(
        original,
        text=altered_text,
        utf8_byte_count=len(altered_text.encode("utf-8")),
        clean_hash=compute_sha256(altered_text),
    )

    problems = verify_pool_offline(assembly.manifest, tampered)
    assert any("content digest mismatch" in p for p in problems)


def test_offline_verification_detects_missing_and_extra_documents() -> None:
    assembly = build_pool(corpus(), views(), binding())

    missing = verify_pool_offline(assembly.manifest, assembly.accepted_documents[1:])
    assert any("absent" in p for p in missing)

    extra = verify_pool_offline(
        assembly.manifest,
        [*assembly.accepted_documents, make_doc("stowaway", "Unexpected text.")],
    )
    assert any("not accepted" in p for p in extra)


def test_pool_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    assembly = build_pool(corpus(), views(), binding())
    path = assembly.manifest.save(tmp_path / "pool_manifest.json")
    reloaded = FrozenPoolManifest.load(path)

    assert reloaded.to_dict() == assembly.manifest.to_dict()
    assert verify_pool_offline(reloaded, assembly.accepted_documents) == []


# ------------------------------------------------------------------- invalidation


def test_changing_a_normalization_rule_invalidates_the_pool() -> None:
    """A cleaning-policy change must produce a different pool identity."""
    baseline = build_pool(corpus(), views(), binding()).manifest
    changed = build_pool(corpus(), views(), binding(cleaning_policy_identity="clean_v2")).manifest

    assert changed.pool_id != baseline.pool_id, "changed normalization reused the pool identity"
    assert changed.binding.identity() != baseline.binding.identity()


def test_changing_a_source_revision_invalidates_the_pool() -> None:
    baseline = build_pool(corpus(), views(), binding()).manifest
    changed = build_pool(
        corpus(), views(), binding(source_revisions={"fixture_src": "rev_2"})
    ).manifest
    assert changed.pool_id != baseline.pool_id


def test_changing_a_view_selector_invalidates_the_pool() -> None:
    """A selector change alters membership and must be visible in the identity."""
    baseline = build_pool(corpus(), views(), binding()).manifest

    altered = views()
    altered[0] = SourceView(
        view_id="prose_view",
        family_id="fixture_family",
        selector=ViewSelector(document_kinds=["prose"], min_utf8_bytes=60),
        declared_raw_byte_share=0.6,
        priority=10,
    )
    changed = build_pool(corpus(), altered, binding()).manifest
    assert changed.pool_id != baseline.pool_id


def test_identical_inputs_reproduce_the_same_pool_identity() -> None:
    """Rebuilding an unchanged pool must not invent a new identity."""
    first = build_pool(corpus(), views(), binding()).manifest
    second = build_pool(corpus(), views(), binding()).manifest
    assert first.pool_id == second.pool_id
    assert first.content_digest == second.content_digest


# ------------------------------------------------------------------------- leaks


def test_pool_refuses_to_freeze_a_leaking_cluster() -> None:
    """A duplicate cluster straddling train and validation is a leak (C05)."""
    docs = corpus()
    docs.append(make_doc("leak_train", PROSE[0], cluster_ids={"duplicate_cluster": "dup_x"}))
    docs.append(
        make_doc(
            "leak_val",
            PROSE[0],
            split="diagnostic_val",
            cluster_ids={"duplicate_cluster": "dup_x"},
        )
    )
    with pytest.raises(LeakDetectedError, match="leaking pool"):
        build_pool(docs, views(), binding())


def test_parent_document_in_another_split_is_a_leak() -> None:
    docs = corpus()
    docs.append(make_doc("parent_doc", PROSE[1], split="diagnostic_val"))
    docs.append(make_doc("child_doc", PROSE[2], parent_ids=["parent_doc"]))

    problems = detect_split_leaks(docs)
    assert any("parent" in p for p in problems)


def test_clean_corpus_reports_no_leaks() -> None:
    assert detect_split_leaks(corpus()) == []


# ------------------------------------------------------------------- sufficiency


def test_insufficient_distinct_text_is_visible() -> None:
    """A budget the pool cannot cover must be reported, not silently repeated."""
    report = assess_sufficiency(
        distinct_train_bytes=1_000, distinct_train_documents=10, planned_budget_tokens=1_000_000
    )
    assert not report.is_sufficient_without_repetition
    assert report.required_epochs > 1
    assert report.repeated_exposure_tokens > 0
    assert any("repeated exposure" in w for w in report.warnings)
    assert "estimate" in report.token_estimate_basis


def test_sufficient_pool_reports_no_repetition() -> None:
    report = assess_sufficiency(
        distinct_train_bytes=40_000_000,
        distinct_train_documents=1000,
        planned_budget_tokens=1_000_000,
    )
    assert report.is_sufficient_without_repetition
    assert report.repeated_exposure_tokens == 0
    assert report.warnings == []


def test_empty_pool_sufficiency_is_reported_not_divided_by_zero() -> None:
    report = assess_sufficiency(0, 0, 1000)
    assert not report.is_sufficient_without_repetition
    assert report.required_epochs == float("inf")
    assert any("no distinct training text" in w for w in report.warnings)


def test_demo_pool_is_tiered_and_labelled() -> None:
    """A tiny pool must not be presentable as production-ready."""
    assembly = build_pool(
        corpus(), views(), binding(), PoolBuildConfig(planned_budget_tokens=100_000)
    )
    manifest = assembly.manifest

    assert manifest.tier == PoolTier.DEMO_PILOT
    assert not manifest.is_production_ready
    assert any("DEMO/PILOT POOL" in note for note in manifest.notes)


def test_tier_is_derived_from_measured_bytes_not_asserted() -> None:
    assert derive_tier(0) is PoolTier.DEMO_PILOT
    assert derive_tier(PRODUCTION_MIN_TRAIN_BYTES - 1) is PoolTier.DEMO_PILOT
    assert derive_tier(PRODUCTION_MIN_TRAIN_BYTES) is PoolTier.PRODUCTION_READY


# -------------------------------------------------------------------- tokenizer


def fit_inputs(assembly: Any) -> tuple[dict[str, list[str]], dict[str, float]]:
    membership = assembly.manifest.view_membership["doc_ids_by_view"]
    train_only = restrict_membership_to_split(membership, assembly.accepted_documents, "train")
    declared = assembly.manifest.view_membership["declared_shares"]
    return train_only, declared


def test_tokenizer_fit_sample_is_training_only() -> None:
    """Fitting on validation text would invalidate every later diagnostic (C05, C06)."""
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)

    manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=5_000),
    )
    split_of = {d.doc_id: d.split for d in assembly.accepted_documents}
    assert all(split_of[doc_id] == "train" for doc_id in manifest.doc_ids)


def test_tokenizer_fit_refuses_non_training_documents() -> None:
    """The guard fires even if a caller skips the restriction helper."""
    assembly = build_pool(corpus(), views(), binding())
    unrestricted = assembly.manifest.view_membership["doc_ids_by_view"]
    leaky = {
        "prose_view": [*unrestricted["prose_view"], "val_0"],
        "code_view": unrestricted["code_view"],
    }
    docs = [*assembly.accepted_documents, make_doc("val_0", PROSE[0], split="diagnostic_val")]

    with pytest.raises(NonTrainingFitError, match="non-training document"):
        build_tokenizer_fit_manifest(docs, leaky, {"prose_view": 1.0}, "pool_x")


def test_tokenizer_fit_is_deterministic_for_a_frozen_seed() -> None:
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)
    config = TokenizerFitConfig(target_sample_bytes=400, seed=7)

    first = build_tokenizer_fit_manifest(
        assembly.accepted_documents, train_only, declared, assembly.manifest.pool_id, config
    )
    second = build_tokenizer_fit_manifest(
        list(reversed(assembly.accepted_documents)),
        train_only,
        declared,
        assembly.manifest.pool_id,
        config,
    )
    assert first.doc_ids == second.doc_ids
    assert first.fit_id == second.fit_id


def test_tokenizer_fit_seed_change_changes_the_sample() -> None:
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)

    a = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=300, seed=1),
    )
    b = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=300, seed=2),
    )
    assert a.policy_identity != b.policy_identity


def test_tokenizer_fit_balances_by_declared_raw_byte_share() -> None:
    """Balance is by bytes, so a view of long documents cannot dominate by size."""
    docs = [make_doc(f"long_{i}", " ".join(PROSE), kind="prose") for i in range(6)]
    docs += [make_doc(f"short_{i}", f"def f{i}(): pass", kind="code") for i in range(20)]

    assembly = build_pool(docs, views(), binding())
    train_only, declared = fit_inputs(assembly)

    manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=1_000),
    )
    assert manifest.declared_shares["prose_view"] == pytest.approx(0.6)
    # Achieved shares are reported alongside declared ones; the deviation is visible
    # rather than silently absorbed.
    assert set(manifest.achieved_shares) == {"prose_view", "code_view"}
    assert 0.0 <= manifest.max_share_deviation() <= 1.0


def test_tokenizer_fit_reports_a_view_that_cannot_meet_its_share() -> None:
    """A thin view must be reported, not quietly under-sampled."""
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)

    manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=10_000_000),
    )
    assert manifest.notes, "a pool too small for the requested sample must say so"
    assert any("does not hold enough text" in note for note in manifest.notes)


def test_tokenizer_fit_resource_plan_is_labelled_an_estimate() -> None:
    """C13: a resource plan is required, and it must not read as a measurement."""
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)
    manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=2_000),
    )

    plan = tokenizer_fit_resource_plan(manifest, 32_768)
    assert plan["target_vocab_size"] == 32_768
    assert plan["network_required"] is False
    assert "estimate" in plan["basis"].lower()
    assert "not a measured" in plan["basis"].lower()
    assert plan["authorization_required"] is False


def test_large_tokenizer_fit_requires_authorization() -> None:
    """A fit above the unattended sample budget must demand authorization (C13)."""
    assembly = build_pool(corpus(), views(), binding())
    train_only, declared = fit_inputs(assembly)
    manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        declared,
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=2_000),
    )
    manifest.total_bytes = 512 * 1024 * 1024
    assert tokenizer_fit_resource_plan(manifest, 32_768)["authorization_required"] is True


# ----------------------------------------------------------------------- regime


def diagnostic_freeze() -> DiagnosticCorpusFreeze:
    return DiagnosticCorpusFreeze(
        diagnostic_doc_ids=["val_0", "val_1"],
        quick_doc_ids=["val_0"],
        diagnostic_bytes=200,
        quick_bytes=100,
    )


def test_quick_subset_must_be_nested_inside_the_diagnostic_corpus() -> None:
    good = diagnostic_freeze()
    good.validate()

    stray = DiagnosticCorpusFreeze(
        diagnostic_doc_ids=["val_0"],
        quick_doc_ids=["val_0", "not_in_diagnostic"],
        diagnostic_bytes=100,
        quick_bytes=150,
    )
    with pytest.raises(RegimeValidationError, match="not nested"):
        stray.validate()


def test_empty_diagnostic_corpus_is_rejected() -> None:
    with pytest.raises(RegimeValidationError, match="empty"):
        DiagnosticCorpusFreeze([], [], 0, 0).validate()


def test_tampered_diagnostic_membership_is_detected() -> None:
    """A diagnostic set cannot be swapped out without invalidating its digest."""
    frozen = diagnostic_freeze()
    frozen.diagnostic_doc_ids = ["val_0", "val_1", "smuggled_in"]
    with pytest.raises(RegimeValidationError, match="digest does not match"):
        frozen.validate()


def test_regime_does_not_bind_a_mixture() -> None:
    """Selecting a mixture later must not look like the regime was already frozen."""
    regime = build_regime(
        pool_id="pool_x",
        pool_content_digest="content_x",
        tokenizer_fingerprint="fp_x",
        tokenizer_fit_id="tokfit_x",
        diagnostic=diagnostic_freeze(),
        exclusion_policy_identity="excl_v1",
        split_policy_identity="split_v1",
    )
    assert regime.binds_a_mixture is False
    assert "mixture" not in {f.lower() for f in regime.to_dict()}


def test_incomplete_regime_is_rejected() -> None:
    with pytest.raises(RegimeValidationError, match="incomplete"):
        build_regime(
            pool_id="",
            pool_content_digest="content_x",
            tokenizer_fingerprint="fp_x",
            tokenizer_fit_id="tokfit_x",
            diagnostic=diagnostic_freeze(),
            exclusion_policy_identity="excl_v1",
            split_policy_identity="split_v1",
        )


def test_changing_only_the_mixture_reuses_pool_and_tokenizer() -> None:
    """The headline acceptance criterion: a mixture change rebuilds nothing."""
    kwargs = {
        "pool_id": "pool_x",
        "pool_content_digest": "content_x",
        "tokenizer_fingerprint": "fp_x",
        "tokenizer_fit_id": "tokfit_x",
        "diagnostic": diagnostic_freeze(),
        "exclusion_policy_identity": "excl_v1",
        "split_policy_identity": "split_v1",
    }
    before = build_regime(**kwargs)  # type: ignore[arg-type]
    after = build_regime(**kwargs)  # type: ignore[arg-type]

    assert regime_accepts_mixture_change(before, after)

    mixture_a = freeze_campaign(before, mixture_id="mix01", exposure_plan_id="plan_a")
    mixture_b = freeze_campaign(after, mixture_id="mix02", exposure_plan_id="plan_b")

    # Different campaigns, identical regime: pool and tokenizer are reused.
    assert mixture_a.campaign_id != mixture_b.campaign_id
    assert mixture_a.regime_id == mixture_b.regime_id
    assert mixture_a.regime_identity_digest == mixture_b.regime_identity_digest


def test_changing_the_pool_does_not_reuse_the_regime() -> None:
    base = {
        "pool_content_digest": "content_x",
        "tokenizer_fingerprint": "fp_x",
        "tokenizer_fit_id": "tokfit_x",
        "diagnostic": diagnostic_freeze(),
        "exclusion_policy_identity": "excl_v1",
        "split_policy_identity": "split_v1",
    }
    before = build_regime(pool_id="pool_x", **base)  # type: ignore[arg-type]
    after = build_regime(pool_id="pool_y", **base)  # type: ignore[arg-type]
    assert not regime_accepts_mixture_change(before, after)


def test_changing_the_tokenizer_does_not_reuse_the_regime() -> None:
    base = {
        "pool_id": "pool_x",
        "pool_content_digest": "content_x",
        "tokenizer_fit_id": "tokfit_x",
        "diagnostic": diagnostic_freeze(),
        "exclusion_policy_identity": "excl_v1",
        "split_policy_identity": "split_v1",
    }
    before = build_regime(tokenizer_fingerprint="fp_x", **base)  # type: ignore[arg-type]
    after = build_regime(tokenizer_fingerprint="fp_y", **base)  # type: ignore[arg-type]
    assert not regime_accepts_mixture_change(before, after)


def test_diagnostic_corpus_does_not_follow_the_winning_mixture() -> None:
    """A trial cannot improve its metric by changing what it is validated on."""
    regime = build_regime(
        pool_id="pool_x",
        pool_content_digest="content_x",
        tokenizer_fingerprint="fp_x",
        tokenizer_fit_id="tokfit_x",
        diagnostic=diagnostic_freeze(),
        exclusion_policy_identity="excl_v1",
        split_policy_identity="split_v1",
    )
    original_digest = regime.diagnostic.membership_digest

    for mixture_id in ("mix01", "mix02", "mix03"):
        campaign = freeze_campaign(
            regime, mixture_id=mixture_id, exposure_plan_id=f"plan_{mixture_id}"
        )
        assert campaign.regime_id == regime.regime_id
        assert regime.diagnostic.membership_digest == original_digest


def test_campaign_freeze_requires_a_mixture_and_exposure_plan() -> None:
    regime = build_regime(
        pool_id="pool_x",
        pool_content_digest="content_x",
        tokenizer_fingerprint="fp_x",
        tokenizer_fit_id="tokfit_x",
        diagnostic=diagnostic_freeze(),
        exclusion_policy_identity="excl_v1",
        split_policy_identity="split_v1",
    )
    assert freeze_campaign(regime, "mix01", "plan_a").binds_a_mixture is True

    with pytest.raises(RegimeValidationError, match="requires both"):
        freeze_campaign(regime, "", "plan_a")
    with pytest.raises(RegimeValidationError, match="requires both"):
        freeze_campaign(regime, "mix01", "")


def test_regime_and_campaign_round_trip_through_disk(tmp_path: Path) -> None:
    regime = build_regime(
        pool_id="pool_x",
        pool_content_digest="content_x",
        tokenizer_fingerprint="fp_x",
        tokenizer_fit_id="tokfit_x",
        diagnostic=diagnostic_freeze(),
        exclusion_policy_identity="excl_v1",
        split_policy_identity="split_v1",
    )
    regime_path = regime.save(tmp_path / "regime.json")
    reloaded = ResearchRegimeManifest.load(regime_path)
    assert reloaded.to_dict() == regime.to_dict()
    reloaded.validate()

    campaign = freeze_campaign(regime, "mix01", "plan_a")
    campaign_path = campaign.save(tmp_path / "campaign.json")
    assert CampaignFreeze.load(campaign_path).to_dict() == campaign.to_dict()
