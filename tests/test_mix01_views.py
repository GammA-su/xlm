"""Acceptance tests for P13: mix01 views, adapters, treatments and run gating.

Covers the prompt: offline schema fixtures per source adapter; exact weight sums;
organic/synthetic separation; English and required-context filters; rejection of
missing sources and empty selectors; a distinct no-IFM treatment identity; and a
pilot that refuses to run without explicit authorization. Live rows are never
touched: every view stays NOT LIVE-VERIFIED here by construction.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters.mix01_adapters import (
    ADAPTERS_BY_ID,
    CommonPileAdapter,
    EssentialWebAdapter,
    FinePdfsAdapter,
    FineWikiAdapter,
    IfmGeneralAdapter,
    IfmPlanningAdapter,
    MissingFieldError,
    NemotronOrganicAdapter,
    RecordRejectedError,
    SimpleStoriesAdapter,
    SynthExplanationsAdapter,
    Txt360WebAdapter,
    WikiRewriteAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id
from xlm.data.pools.views import SourceView, ViewSelector, resolve_view_membership
from xlm.data.sources.mix01 import (
    ComponentReadiness,
    Mix01BlockedError,
    MixturePreset,
    PilotAuthorization,
    PilotNotAuthorizedError,
    PresetValidationError,
    build_source_views,
    diff_presets,
    gate_preset_for_run,
    load_mix01_views,
    load_mixture_preset,
    mix01_status,
    validate_preset_components,
    validate_preset_weights_exact,
)
from xlm.data.sources.schema import FieldDescriptor, ViewSchema

REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = REPO_ROOT / "recipes" / "mixtures"
VIEWS_PATH = PRESET_DIR / "mix01_views.yaml"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "mixture" / "views"

PRESET_FILES = [
    "mix01.yaml",
    "m1_less_synth.yaml",
    "m2_more_synth.yaml",
    "m3_more_pdfs.yaml",
    "m4_txt360_web.yaml",
    "m5_more_practical.yaml",
    "mix01_no_ifm.yaml",
]


def read_fixture(name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def adapt_all() -> list[CanonicalDocument]:
    """Adapt every fixture row that its view accepts; rejections stay rejected."""
    docs: list[CanonicalDocument] = []
    plans: list[tuple[str, object]] = [
        ("nemotron_organic.jsonl", NemotronOrganicAdapter("High-Quality")),
        ("nemotron_organic.jsonl", NemotronOrganicAdapter("Medium-High-Quality")),
        ("synth_en.jsonl", SynthExplanationsAdapter()),
        ("wiki_rewrite.jsonl", WikiRewriteAdapter()),
        ("finewiki_en.jsonl", FineWikiAdapter()),
        ("finepdfs_en.jsonl", FinePdfsAdapter()),
        ("ifm_general.jsonl", IfmGeneralAdapter()),
        ("ifm_planning.jsonl", IfmPlanningAdapter()),
        ("common_pile.jsonl", CommonPileAdapter()),
        ("simple_stories.jsonl", SimpleStoriesAdapter()),
        ("txt360_web.jsonl", Txt360WebAdapter()),
    ]
    for filename, adapter in plans:
        for row_index, record in enumerate(read_fixture(filename)):
            try:
                docs.append(
                    adapter.adapt(  # type: ignore[attr-defined]
                        record,
                        source_file=filename,
                        source_row=row_index,
                        source_revision="fixture_rev",
                    )
                )
            except (MissingFieldError, RecordRejectedError):
                continue
    # Essential-Web slices are explicit operator configuration, not inference:
    # cycle fixture rows across the three supported components so every slice
    # view selects real-shaped rows.
    essential_components = (
        EssentialWebAdapter("essential_science"),
        EssentialWebAdapter("essential_practical"),
        EssentialWebAdapter("essential_prose"),
    )
    for row_index, record in enumerate(read_fixture("essential_web.jsonl")):
        adapter = essential_components[row_index % len(essential_components)]
        try:
            docs.append(
                adapter.adapt(
                    record,
                    source_file="essential_web.jsonl",
                    source_row=row_index,
                    source_revision="fixture_rev",
                )
            )
        except (MissingFieldError, RecordRejectedError):
            continue
    return docs


# ------------------------------------------------------------- preset pack


@pytest.mark.parametrize("filename", PRESET_FILES)
def test_all_pack_presets_sum_exactly_to_one(filename: str) -> None:
    """Every treatment's shares must partition the budget exactly, not approximately."""
    preset = load_mixture_preset(PRESET_DIR / filename)
    assert validate_preset_weights_exact(preset) == Fraction(1)
    assert preset.weight_sum_exact() == Fraction(1)


def test_preset_rejects_silent_renormalization_and_lax_missing_policy() -> None:
    base = load_mixture_preset(PRESET_DIR / "mix01.yaml").model_dump()
    with pytest.raises(ValueError, match="silent renormalization"):
        MixturePreset.model_validate({**base, "allow_silent_renormalization": True})
    with pytest.raises(ValueError, match="only 'error' is supported"):
        MixturePreset.model_validate({**base, "missing_source_policy": "skip"})


def test_preset_rejects_a_nonexact_sum() -> None:
    preset = load_mixture_preset(PRESET_DIR / "mix01.yaml").model_copy(
        update={
            "weights": {
                **load_mixture_preset(PRESET_DIR / "mix01.yaml").weights,
                "simple_stories": 0.019,
            }
        }
    )
    with pytest.raises(PresetValidationError, match="not 1"):
        validate_preset_weights_exact(preset)


def test_m0_m5_treatment_diffs_match_pack_intent() -> None:
    """M0-M5 diffs hold every other setting fixed and move exactly the stated shares."""
    base = load_mixture_preset(PRESET_DIR / "mix01.yaml")

    less = diff_presets(base, load_mixture_preset(PRESET_DIR / "m1_less_synth.yaml"))
    assert less.weight_delta("synth_en_explanations") == pytest.approx(-0.10)
    assert less.weight_delta("essential_science") == pytest.approx(0.05)
    assert less.weight_delta("essential_practical") == pytest.approx(0.05)
    assert less.weight_delta("essential_prose") == pytest.approx(0.0)

    more = diff_presets(base, load_mixture_preset(PRESET_DIR / "m2_more_synth.yaml"))
    assert more.weight_delta("synth_en_explanations") == pytest.approx(0.15)
    assert more.weight_delta("essential_science") == pytest.approx(-0.06)
    assert more.weight_delta("essential_practical") == pytest.approx(-0.06)
    assert more.weight_delta("essential_prose") == pytest.approx(-0.03)

    pdfs = diff_presets(base, load_mixture_preset(PRESET_DIR / "m3_more_pdfs.yaml"))
    assert pdfs.weight_delta("finepdfs_en") == pytest.approx(0.10)
    assert pdfs.weight_delta("nemotron_organic_high") == pytest.approx(-0.075)
    assert pdfs.weight_delta("nemotron_organic_medium_high") == pytest.approx(-0.025)

    web = diff_presets(base, load_mixture_preset(PRESET_DIR / "m4_txt360_web.yaml"))
    assert web.removed == {"nemotron_organic_high": 0.15, "nemotron_organic_medium_high": 0.05}
    assert web.added == {"txt360_web": 0.2}
    assert web.changed == {}

    practical = diff_presets(base, load_mixture_preset(PRESET_DIR / "m5_more_practical.yaml"))
    assert practical.weight_delta("essential_science") == pytest.approx(-0.05)
    assert practical.weight_delta("essential_practical") == pytest.approx(0.05)
    assert set(practical.changed) == {"essential_science", "essential_practical"}


def test_no_ifm_is_an_explicit_alternative_not_a_fallback() -> None:
    """The no-IFM treatment has its own ID and hash, moving IFM 5% to practical prose."""
    base = load_mixture_preset(PRESET_DIR / "mix01.yaml")
    alternative = load_mixture_preset(PRESET_DIR / "mix01_no_ifm.yaml")

    assert alternative.id == "mix01_no_ifm"
    assert alternative.id != base.id
    assert alternative.identity() != base.identity()

    diff = diff_presets(base, alternative)
    assert diff.removed == {"ifm_behaviors_general_planning": 0.05}
    assert diff.added == {}
    assert diff.weight_delta("essential_practical") == pytest.approx(0.05)
    assert set(diff.changed) == {"essential_practical"}


def test_all_treatment_identities_are_distinct() -> None:
    presets = [load_mixture_preset(PRESET_DIR / f) for f in PRESET_FILES]
    identities = [p.identity() for p in presets]
    assert len(set(identities)) == len(presets), "two treatments share an identity hash"


def test_unknown_component_blocks_without_fallback() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    preset = load_mixture_preset(PRESET_DIR / "mix01.yaml").model_copy(
        update={"weights": {"essential_science": 0.5, "mystery_source": 0.5}}
    )
    with pytest.raises(PresetValidationError, match="undeclared mix01 components"):
        validate_preset_components(preset, registry)


# ------------------------------------------------------------------- registry


def test_registry_loads_and_covers_every_preset_component() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    assert len(registry.views) == 13
    declared = {v.component_id for v in registry.views}
    for filename in PRESET_FILES:
        preset = load_mixture_preset(PRESET_DIR / filename)
        validate_preset_components(preset, registry)
        assert set(preset.weights) <= declared


def test_registry_selectors_use_observed_upstream_names() -> None:
    """Selectors must use probed config names, never assumed category names."""
    registry = load_mix01_views(VIEWS_PATH)
    by_id = {v.component_id: v for v in registry.views}

    assert by_id["nemotron_organic_high"].upstream_selector["config_name"] == "High-Quality"
    assert (
        by_id["nemotron_organic_medium_high"].upstream_selector["config_name"]
        == "Medium-High-Quality"
    )
    for view_id in ("nemotron_organic_high", "nemotron_organic_medium_high"):
        assert "High-Quality" in by_id[view_id].observed_configs
        assert "High-Quality-Synthetic" in by_id[view_id].observed_configs
    assert by_id["nemotron_wiki_rewrite"].upstream_selector["config_name"] == (
        "Nemotron-Pretraining-Wiki-Rewrite"
    )
    assert by_id["finewiki_en"].upstream_selector["config_name"] == "en"
    assert by_id["finepdfs_en"].upstream_selector["config_name"] == "eng_Latn"
    assert by_id["synth_en_explanations"].upstream_selector["config_name"] == "default"
    assert by_id["txt360_web"].upstream_selector["config_name"] == "web-high-medium"
    assert by_id["ifm_behaviors_general_planning"].upstream_selector["config_names"] == [
        "general",
        "planning",
    ]


def test_view_selectors_are_nonempty_over_adapted_fixtures() -> None:
    """Every mix01 selector must match real adapted rows; an empty view blocks the run."""
    registry = load_mix01_views(VIEWS_PATH)
    docs = adapt_all()
    # 20: the finewiki fixture carries two adaptable rows (live H1 shape plus
    # one legacy no-heading shape), each ifm fixture two (declared
    # token_count plus one bare-text row), the finepdfs fixture two (a
    # valid Docling row plus a mixed-language eng_Latn Docling row that the
    # routing label still accepts), and the essential_web fixture three
    # real-shaped rows cycled across the three explicit slice components;
    # every other count is unchanged.
    assert len(docs) == 20

    membership = resolve_view_membership(docs, build_source_views(registry))
    for view in registry.views:
        matched = membership.doc_ids_by_view[view.component_id]
        assert matched, f"view '{view.component_id}' selected nothing: empty views must block"


def test_empty_selector_is_refused_by_gating() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    docs = adapt_all()
    bogus = SourceView(
        view_id="essential_science",
        family_id="essential_web",
        selector=ViewSelector(
            languages=["en"], metadata_equals={"mix01_component": "no_such_component"}
        ),
    )
    membership = resolve_view_membership(docs, [bogus])
    assert membership.doc_ids_by_view["essential_science"] == []

    preset = load_mixture_preset(PRESET_DIR / "mix01.yaml")
    with pytest.raises(Mix01BlockedError, match="insufficient|zero available"):
        gate_preset_for_run(
            preset,
            registry,
            admission={v.source_id: "admitted" for v in registry.views},
            availability={"essential_science": 0},
        )


# ------------------------------------------------------------------- adapters


def test_essential_web_live_contract_and_explicit_slices() -> None:
    rows = read_fixture("essential_web.jsonl")
    science = EssentialWebAdapter("essential_science")
    assert science.component == "essential_science"
    with pytest.raises(ValueError, match="explicit operator configuration"):
        EssentialWebAdapter("taxonomy:science")

    first = science.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert first.source_metadata["mix01_component"] == "essential_science"
    assert first.source_metadata["upstream_id"] == 111
    assert first.source_metadata["pid"] == "authored-pid-0"
    assert first.source_metadata["fasttext_english"] == 0.97
    assert first.source_metadata["document_type_v2_primary"] == "Personal Blog"
    assert first.source_id == "essential_web"
    assert first.language == "en"
    assert "taxonomy" not in first.source_metadata
    assert "quality_tier" not in first.source_metadata

    # Same row under another explicit slice: same document, other assignment.
    practical = EssentialWebAdapter("essential_practical").adapt(
        rows[0], source_file="f", source_row=0, source_revision="r"
    )
    assert practical.doc_id == first.doc_id
    assert practical.source_metadata["mix01_component"] == "essential_practical"

    # Old flat-taxonomy shapes are refused as wrong-schema evidence.
    with pytest.raises(MissingFieldError, match="'eai_taxonomy'"):
        science.adapt(
            {"text": "t", "taxonomy": "science", "quality_tier": "high", "language": "en"},
            source_file="f",
            source_row=0,
            source_revision="r",
        )
    with pytest.raises(MissingFieldError, match="'quality_signals'"):
        science.adapt(
            {"text": "t", "eai_taxonomy": {}, "id": 1, "pid": "p", "metadata": {}},
            source_file="f",
            source_row=0,
            source_revision="r",
        )


def test_nemotron_organic_synthetic_separation() -> None:
    rows = read_fixture("nemotron_organic.jsonl")
    high = NemotronOrganicAdapter("High-Quality")
    medium_high = NemotronOrganicAdapter("Medium-High-Quality")

    kept = high.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert kept.source_metadata["mix01_component"] == "nemotron_organic_high"
    assert kept.source_metadata["quality_category"] == "High-Quality"

    kept_medium = medium_high.adapt(rows[1], source_file="f", source_row=1, source_revision="r")
    assert kept_medium.source_metadata["mix01_component"] == "nemotron_organic_medium_high"

    # Synthetic rows are rejected from BOTH organic slices, by exact category match.
    for adapter in (high, medium_high):
        with pytest.raises(RecordRejectedError, match="selects .* only"):
            adapter.adapt(rows[2], source_file="f", source_row=2, source_revision="r")
        with pytest.raises(RecordRejectedError, match="selects .* only"):
            adapter.adapt(rows[3], source_file="f", source_row=3, source_revision="r")
    with pytest.raises(MissingFieldError, match="quality_category"):
        high.adapt(rows[4], source_file="f", source_row=4, source_revision="r")

    with pytest.raises(ValueError, match="exact organic category"):
        NemotronOrganicAdapter("High-Quality-Synthetic")


def test_synth_requires_context_and_rejects_traces() -> None:
    adapter = SynthExplanationsAdapter()
    rows = read_fixture("synth_en.jsonl")
    doc = adapter.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert "Context:" in doc.text and "Question:" in doc.text and "Explanation:" in doc.text
    assert doc.source_metadata["has_context"] is True

    with pytest.raises(MissingFieldError, match="context"):
        adapter.adapt(rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(RecordRejectedError, match="separate treatment"):
        adapter.adapt(rows[2], source_file="f", source_row=2, source_revision="r")
    with pytest.raises(RecordRejectedError, match="rows only"):
        adapter.adapt(rows[3], source_file="f", source_row=3, source_revision="r")


def test_english_and_source_filters() -> None:
    wiki = FineWikiAdapter()
    rows = read_fixture("finewiki_en.jsonl")
    doc = wiki.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    # Live schema: the upstream text already carries the "# {title}" heading,
    # so the adapter must not duplicate the title.
    assert doc.text == rows[0]["text"]
    assert doc.text.split("\n", 1)[0] == "# Aqueduct"
    assert not doc.text.startswith("Aqueduct\n\n")
    assert doc.language == "en"
    assert doc.source_id == "finewiki"
    assert doc.license_reference == "cc-by-sa-4.0"
    assert doc.source_metadata["config_name"] == "en"
    with pytest.raises(RecordRejectedError, match="rows only"):
        wiki.adapt(rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(MissingFieldError, match="'text'"):
        wiki.adapt(rows[2], source_file="f", source_row=2, source_revision="r")
    with pytest.raises(MissingFieldError, match="'in_language'"):
        wiki.adapt(rows[4], source_file="f", source_row=4, source_revision="r")


def test_finewiki_title_rendering_without_heading() -> None:
    """A record whose text lacks the exact H1 keeps the legacy prepend rendering."""
    wiki = FineWikiAdapter()
    rows = read_fixture("finewiki_en.jsonl")
    doc = wiki.adapt(rows[3], source_file="f", source_row=3, source_revision="r")
    assert doc.text == "Bare effigy\n\nProse without a heading."
    # Near-miss headings must not suppress the prepend: only an exact
    # first-line "# {title}" match counts, never a fuzzy guess.
    near = wiki.adapt(
        {"title": "Aqueduct", "text": "#Aqueduct\nNo space after hash.", "in_language": "en"},
        source_file="f",
        source_row=9,
        source_revision="r",
    )
    assert near.text == "Aqueduct\n\n#Aqueduct\nNo space after hash."

    pdfs = FinePdfsAdapter()
    pdf_rows = read_fixture("finepdfs_en.jsonl")
    pdfs.adapt(pdf_rows[0], source_file="f", source_row=0, source_revision="r")
    with pytest.raises(RecordRejectedError, match="rows only"):
        pdfs.adapt(pdf_rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(RecordRejectedError, match="RolmOCR image-based OCR"):
        pdfs.adapt(pdf_rows[2], source_file="f", source_row=2, source_revision="r")


def test_ifm_subset_adapters_are_independent() -> None:
    general = IfmGeneralAdapter()
    planning = IfmPlanningAdapter()
    general_rows = read_fixture("ifm_general.jsonl")
    planning_rows = read_fixture("ifm_planning.jsonl")

    general_doc = general.adapt(general_rows[0], source_file="f", source_row=0, source_revision="r")
    planning_doc = planning.adapt(
        planning_rows[0], source_file="f", source_row=0, source_revision="r"
    )
    assert general_doc.source_metadata["subset"] == "general"
    assert planning_doc.source_metadata["subset"] == "planning"
    assert (
        general_doc.source_metadata["mix01_component"]
        == planning_doc.source_metadata["mix01_component"]
        == "ifm_behaviors_general_planning"
    )

    # Both subsets share the live {text, token_count} shape, so a valid text
    # row adapts under either adapter by design — but each stamps its own
    # subset, doc_id, and adapter-bound lineage. Malformed rows stay rejected.
    cross = planning.adapt(general_rows[0], source_file="f", source_row=0, source_revision="r")
    assert cross.source_metadata["subset"] == "planning"
    assert cross.doc_id == canonical_source_doc_id("ifm_behaviors:planning", "f", 0)
    with pytest.raises(MissingFieldError, match="'text'"):
        general.adapt(general_rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(MissingFieldError, match="'text'"):
        general.adapt(planning_rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(MissingFieldError, match="'text'"):
        planning.adapt(general_rows[1], source_file="f", source_row=1, source_revision="r")


def test_ifm_planning_live_schema() -> None:
    """Pinned live contract: verbatim text, declared token_count, view-level en."""
    planning = IfmPlanningAdapter()
    rows = read_fixture("ifm_planning.jsonl")

    doc = planning.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert doc.text == rows[0]["text"]
    assert "Goal:" not in doc.text and "- " not in doc.text.split("\n", 1)[0]
    assert "Plan:" not in doc.text
    assert doc.language == "en"
    assert doc.source_metadata["language_provenance"] == "view English-only (mix01 registry)"
    assert doc.source_metadata["subset"] == "planning"
    assert doc.source_metadata["upstream_token_count"] == 21
    assert doc.source_id == "ifm_behaviors"
    assert doc.license_reference == "apache-2.0"
    assert doc.doc_id == canonical_source_doc_id("ifm_behaviors:planning", "f", 0)

    with pytest.raises(MissingFieldError, match="'text'"):
        planning.adapt(rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(MissingFieldError, match="non-empty"):
        planning.adapt(rows[2], source_file="f", source_row=2, source_revision="r")

    # The stale goal/plan_steps shape is refused as wrong-schema evidence.
    with pytest.raises(MissingFieldError, match="'text'"):
        planning.adapt(rows[3], source_file="f", source_row=3, source_revision="r")

    with pytest.raises(MissingFieldError, match="token_count"):
        planning.adapt(rows[4], source_file="f", source_row=4, source_revision="r")

    bare = planning.adapt(rows[5], source_file="f", source_row=5, source_revision="r")
    assert "upstream_token_count" not in bare.source_metadata
    assert bare.text == rows[5]["text"]

    again = planning.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert again.to_dict() == doc.to_dict()


def test_ifm_subsets_stay_independent() -> None:
    """General and Planning keep distinct adapter IDs, subsets, and doc IDs."""
    general_rows = read_fixture("ifm_general.jsonl")
    planning_rows = read_fixture("ifm_planning.jsonl")
    general_doc = IfmGeneralAdapter().adapt(
        general_rows[0], source_file="f", source_row=0, source_revision="r"
    )
    planning_doc = IfmPlanningAdapter().adapt(
        planning_rows[0], source_file="f", source_row=0, source_revision="r"
    )
    assert IfmGeneralAdapter.ADAPTER_ID == "ifm_general"
    assert IfmPlanningAdapter.ADAPTER_ID == "ifm_planning"
    assert general_doc.source_metadata["subset"] == "general"
    assert planning_doc.source_metadata["subset"] == "planning"
    assert general_doc.doc_id != planning_doc.doc_id
    assert (
        general_doc.source_metadata["mix01_component"]
        == planning_doc.source_metadata["mix01_component"]
        == "ifm_behaviors_general_planning"
    )


def test_ifm_general_live_schema() -> None:
    """Pinned live contract: verbatim text, declared token_count, view-level en."""
    general = IfmGeneralAdapter()
    rows = read_fixture("ifm_general.jsonl")

    doc = general.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert doc.text == rows[0]["text"]
    assert "Instruction:" not in doc.text and "Response:" not in doc.text
    assert doc.language == "en"
    assert doc.source_metadata["language_provenance"] == "view English-only (mix01 registry)"
    assert doc.source_metadata["upstream_token_count"] == 14
    assert doc.source_id == "ifm_behaviors"
    assert doc.license_reference == "apache-2.0"
    assert doc.doc_id == canonical_source_doc_id("ifm_behaviors:general", "f", 0)

    # Missing and empty text are refused, never defaulted.
    with pytest.raises(MissingFieldError, match="'text'"):
        general.adapt(rows[1], source_file="f", source_row=1, source_revision="r")
    with pytest.raises(MissingFieldError, match="non-empty"):
        general.adapt(rows[2], source_file="f", source_row=2, source_revision="r")

    # The legacy instruction/response shape is refused as wrong-schema evidence.
    with pytest.raises(MissingFieldError, match="'text'"):
        general.adapt(rows[3], source_file="f", source_row=3, source_revision="r")

    # A malformed token_count is refused, not coerced.
    with pytest.raises(MissingFieldError, match="token_count"):
        general.adapt(rows[4], source_file="f", source_row=4, source_revision="r")

    # Absent token_count simply omits the metadata key.
    bare = general.adapt(rows[5], source_file="f", source_row=5, source_revision="r")
    assert "upstream_token_count" not in bare.source_metadata
    assert bare.text == rows[5]["text"]

    # Deterministic re-adaptation produces identical documents.
    again = general.adapt(rows[0], source_file="f", source_row=0, source_revision="r")
    assert again.to_dict() == doc.to_dict()


def test_remaining_adapters_render_and_reject() -> None:
    revision, source_file = "r", "f"
    wiki_rows = read_fixture("wiki_rewrite.jsonl")
    assert (
        WikiRewriteAdapter()
        .adapt(wiki_rows[0], source_file=source_file, source_row=0, source_revision=revision)
        .source_metadata["mix01_component"]
        == "nemotron_wiki_rewrite"
    )
    with pytest.raises(MissingFieldError, match="'text'"):
        WikiRewriteAdapter().adapt(
            wiki_rows[2], source_file=source_file, source_row=2, source_revision=revision
        )

    pile_rows = read_fixture("common_pile.jsonl")
    assert (
        CommonPileAdapter()
        .adapt(pile_rows[0], source_file=source_file, source_row=0, source_revision=revision)
        .source_metadata["mix01_component"]
        == "common_pile_prose"
    )
    with pytest.raises(MissingFieldError, match="'text'"):
        CommonPileAdapter().adapt(
            pile_rows[1], source_file=source_file, source_row=1, source_revision=revision
        )

    story_rows = read_fixture("simple_stories.jsonl")
    assert (
        SimpleStoriesAdapter()
        .adapt(story_rows[0], source_file=source_file, source_row=0, source_revision=revision)
        .source_metadata["mix01_component"]
        == "simple_stories"
    )
    with pytest.raises(MissingFieldError, match="'story'"):
        SimpleStoriesAdapter().adapt(
            story_rows[2], source_file=source_file, source_row=2, source_revision=revision
        )

    txt_rows = read_fixture("txt360_web.jsonl")
    assert (
        Txt360WebAdapter()
        .adapt(txt_rows[0], source_file=source_file, source_row=0, source_revision=revision)
        .source_metadata["mix01_component"]
        == "txt360_web"
    )
    with pytest.raises(RecordRejectedError, match="web-high-medium.*only"):
        Txt360WebAdapter().adapt(
            txt_rows[1], source_file=source_file, source_row=1, source_revision=revision
        )
    with pytest.raises(MissingFieldError, match="quality_band"):
        Txt360WebAdapter().adapt(
            txt_rows[2], source_file=source_file, source_row=2, source_revision=revision
        )


def test_adapter_contracts_detect_missing_fields() -> None:
    """Each adapter's extractor contract must fail exactly on its required fields."""
    cases = [
        ("essential_web.jsonl", EssentialWebAdapter("essential_science")),
        ("nemotron_organic.jsonl", NemotronOrganicAdapter("High-Quality")),
        ("synth_en.jsonl", SynthExplanationsAdapter()),
        ("wiki_rewrite.jsonl", WikiRewriteAdapter()),
        ("finewiki_en.jsonl", FineWikiAdapter()),
        ("finepdfs_en.jsonl", FinePdfsAdapter()),
        ("ifm_general.jsonl", IfmGeneralAdapter()),
        ("ifm_planning.jsonl", IfmPlanningAdapter()),
        ("common_pile.jsonl", CommonPileAdapter()),
        ("simple_stories.jsonl", SimpleStoriesAdapter()),
        ("txt360_web.jsonl", Txt360WebAdapter()),
    ]
    for filename, adapter in cases:
        keys: set[str] = set()
        for record in read_fixture(filename):
            keys.update(record.keys())
        schema = ViewSchema(
            view_id="fixture",
            fields={k: FieldDescriptor(name=k, type_name="string") for k in keys},
            raw_schema_type="jsonl",
        )
        contract = adapter.contract()  # type: ignore[attr-defined]
        is_valid, _ = contract.evaluate_against_schema(schema)
        assert is_valid, f"contract for {filename} must accept the full fixture schema"

        for required in contract.required_fields:
            pruned = ViewSchema(
                view_id="fixture",
                fields={k: v for k, v in schema.fields.items() if k != required.split(".")[0]},
                raw_schema_type="jsonl",
            )
            still_valid, missing = contract.evaluate_against_schema(pruned)
            assert not still_valid and missing, (
                f"contract for {filename} must flag a missing '{required}'"
            )


# ------------------------------------------------------------------- gating


def test_denied_repository_blocks_with_no_substitution() -> None:
    """A view bound to a FineWeb-family repository is blocked, never rerouted (A13)."""
    registry = load_mix01_views(VIEWS_PATH)
    finewiki = next(v for v in registry.views if v.component_id == "finewiki_en")
    poisoned = finewiki.model_copy(update={"repository": "HuggingFaceFW/fineweb"})
    report = mix01_status(
        registry.model_copy(update={"views": [poisoned]}),
        admission={"finewiki": "admitted"},
    )
    assert len(report) == 1
    assert report[0].readiness is ComponentReadiness.BLOCKED
    assert "denied by XLM policy" in " ".join(report[0].reasons)


def test_unadmitted_mixture_fails_loudly_instead_of_falling_back() -> None:
    """mix01 with blocked IFM raises; the no-IFM preset is never applied automatically."""
    registry = load_mix01_views(VIEWS_PATH)
    preset = load_mixture_preset(PRESET_DIR / "mix01.yaml")
    with pytest.raises(Mix01BlockedError, match="No silent fallback"):
        gate_preset_for_run(preset, registry, admission={})


def test_m4_txt360_variant_blocks_until_its_view_is_admitted() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    preset = load_mixture_preset(PRESET_DIR / "m4_txt360_web.yaml")
    with pytest.raises(Mix01BlockedError, match="txt360_web"):
        gate_preset_for_run(preset, registry, admission={})


def test_status_reports_not_live_verified_without_evidence() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    report = mix01_status(registry)
    assert len(report) == 13
    for status in report:
        assert status.readiness is ComponentReadiness.NOT_LIVE_VERIFIED
        assert status.reasons


def test_fully_evidenced_views_report_ready() -> None:
    """The gate is satisfiable in principle: evidence plus admission yields READY."""
    registry = load_mix01_views(VIEWS_PATH)
    evidenced = registry.model_copy(
        update={
            "views": [
                v.model_copy(update={"live_verified": True})
                for v in registry.views
                if v.component_id == "simple_stories"
            ]
        }
    )
    report = mix01_status(
        evidenced,
        admission={"simple_stories": "admitted"},
        availability={"simple_stories": 500},
    )
    stories = next(s for s in report if s.component_id == "simple_stories")
    assert stories.readiness is ComponentReadiness.READY


def test_registry_rejects_duplicate_components_and_unknown_adapters() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    with pytest.raises(ValueError, match="duplicate component_id"):
        type(registry).model_validate(
            {**registry.model_dump(), "views": [*registry.views, registry.views[0]]}
        )
    for view in registry.views:
        for adapter_id in [view.adapter_id, *view.extra_adapter_ids]:
            assert adapter_id in ADAPTERS_BY_ID, f"no tested adapter '{adapter_id}'"


# --------------------------------------------------------------------- pilot


def test_pilot_refused_without_an_authorization_envelope() -> None:
    with pytest.raises(PilotNotAuthorizedError, match="no explicit operator authorization"):
        PilotAuthorization().require("finewiki", planned_documents=10, planned_bytes=10_000)


def test_pilot_refused_for_unallowlisted_sources_and_over_cap() -> None:
    envelope = PilotAuthorization(
        operator_authorized=True,
        allowlisted_source_ids=("finewiki",),
        max_documents=100,
        max_bytes=1_000_000,
        authorized_by="operator",
        ticket="P13-PILOT-001",
    )
    envelope.require("finewiki", planned_documents=100, planned_bytes=1_000_000)
    with pytest.raises(PilotNotAuthorizedError, match="not in the authorized allowlist"):
        envelope.require("synth", planned_documents=10, planned_bytes=10_000)
    with pytest.raises(PilotNotAuthorizedError, match="exceed the authorized aggregate cap"):
        envelope.require("finewiki", planned_documents=101, planned_bytes=10_000)
    with pytest.raises(PilotNotAuthorizedError, match="exceed the authorized aggregate cap"):
        envelope.require("finewiki", planned_documents=10, planned_bytes=1_000_001)


# ------------------------------------------------------- data adapt CLI seam


LIVE_REVISION = "8bd13e72e6a002407649b3e898535f42ceb1aeb9"
LIVE_FILE = "data/enwiki/000_00013.parquet"


def _adapt_plan(tmp_path: Path) -> Path:
    """Authored pilot-shaped plan mirroring the observed live selection."""
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        PlanAuthorization,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_adapt_fixture",
        source_id="finewiki",
        view_id="en",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision=LIVE_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[LIVE_FILE],
        row_ranges={LIVE_FILE: (0, 3)},
        output_artifact_id="raw_finewiki_en",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=16 * 1024**2, max_records=100),
        authorization=PlanAuthorization(
            authorization_hash="",
            authorized_by="closeout-fixture",
            authorized_at="2026-09-21T00:00:00Z",
            scope="pilot",
            is_pilot_approved=True,
        ),
    )
    authorized = plan.model_copy(
        update={
            "authorization": plan.authorization.model_copy(
                update={"authorization_hash": plan.compute_behavioral_hash()}
            )
        }
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(authorized, plan_path)
    return plan_path


def _adapt_records(plan_hash: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = []
    for row_index, row in enumerate(rows):
        lines.append(
            json.dumps(
                {
                    **row,
                    "_xlm_acquisition": {
                        "source_id": "finewiki",
                        "repository": "HuggingFaceFW/finewiki",
                        "revision": LIVE_REVISION,
                        "source_file": LIVE_FILE,
                        "row_index": row_index,
                        "selection_hash": plan_hash,
                    },
                },
                ensure_ascii=False,
            )
        )
    return lines


def _live_shaped_rows() -> list[dict[str, Any]]:
    return [
        {"title": "T1", "text": "# T1\nBody one.", "in_language": "en"},
        {"title": "T2", "text": "# T2\nBody two.", "in_language": "en"},
    ]


def _run_adapt(home: Path, *args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "XLM_HOME": str(home)},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def test_data_adapt_selected_records_to_canonical(tmp_path: Path) -> None:
    """Verified selected rows resolve through the registry adapter to JSONL."""
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _adapt_plan(tmp_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text(
        "\n".join(_adapt_records(plan_hash, _live_shaped_rows())) + "\n", encoding="utf-8"
    )
    out_dir = tmp_path / "canonical"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finewiki_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    docs = [
        json.loads(line)
        for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(docs) == 2
    assert docs[0]["text"] == "# T1\nBody one."
    assert docs[0]["language"] == "en"
    assert docs[0]["source_id"] == "finewiki"
    assert docs[0]["source_revision"] == LIVE_REVISION
    assert docs[0]["source_file"] == LIVE_FILE
    assert docs[0]["source_row"] == 0
    assert docs[0]["license_reference"] == "cc-by-sa-4.0"
    # Deterministic re-run produces identical bytes.
    out_dir2 = tmp_path / "canonical2"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finewiki_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir2),
    )
    assert (out_dir2 / "documents.jsonl").read_bytes() == (out_dir / "documents.jsonl").read_bytes()


def test_data_adapt_refusals(tmp_path: Path) -> None:
    """Foreign locators, unknown adapters, and rejected rows fail closed."""
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _adapt_plan(tmp_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    home = tmp_path / "home"

    def attempt(
        rows: list[str], output: Path, adapter: str = "finewiki_en"
    ) -> subprocess.CompletedProcess[str]:
        selected = tmp_path / f"sel_{output.name}.jsonl"
        selected.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return _run_adapt(
            home,
            "data",
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            adapter,
            "--input",
            str(selected),
            "--output-dir",
            str(output),
            success=False,
        )

    good = _adapt_records(plan_hash, _live_shaped_rows())
    assert "unknown adapter" in attempt(good, tmp_path / "o1", adapter="nope").stderr
    assert (
        "no _xlm_acquisition locator"
        in attempt([json.dumps(_live_shaped_rows()[0])], tmp_path / "o2").stderr
    )
    foreign = _adapt_records("0" * 64, _live_shaped_rows())
    assert "selection_hash" in attempt(foreign, tmp_path / "o3").stderr
    non_en = _adapt_records(
        plan_hash,
        [{"title": "T9", "text": "# T9\nTexte.", "in_language": "fr"}],
    )
    assert "refused line 1" in attempt(non_en, tmp_path / "o4").stderr
    # Existing output is never silently overwritten.
    out_dir = tmp_path / "o5"
    selected = tmp_path / "sel_o5.jsonl"
    selected.write_text("\n".join(good) + "\n", encoding="utf-8")
    _run_adapt(
        home,
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finewiki_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    assert "refusing to overwrite" in attempt(good, out_dir).stderr


IFM_LIVE_REVISION = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"
IFM_LIVE_FILE = "general/general_full.chunk0-bdbff8a5c6-00315.parquet"


def test_data_adapt_ifm_general_live_shape(tmp_path: Path) -> None:
    """The existing seam adapts live-shaped {text, token_count} rows offline."""
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        PlanAuthorization,
        load_acquisition_plan,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_adapt_ifm_fixture",
        source_id="ifm_behaviors",
        view_id="general",
        provider="huggingface",
        repository="IFM/Pretrain-Behaviors",
        revision=IFM_LIVE_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[IFM_LIVE_FILE],
        row_ranges={IFM_LIVE_FILE: (0, 3)},
        output_artifact_id="raw_ifm_behaviors_general",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=16 * 1024**2, max_records=100),
        authorization=PlanAuthorization(
            authorization_hash="",
            authorized_by="closeout-fixture",
            authorized_at="2026-09-21T00:00:00Z",
            scope="pilot",
            is_pilot_approved=True,
        ),
    )
    authorized = plan.model_copy(
        update={
            "authorization": plan.authorization.model_copy(
                update={"authorization_hash": plan.compute_behavioral_hash()}
            )
        }
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(authorized, plan_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()

    live_rows = [
        {"text": "First complete training document.", "token_count": 7},
        {"text": "Second complete training document.", "token_count": 9},
    ]
    lines = [
        json.dumps(
            {
                **row,
                "_xlm_acquisition": {
                    "source_id": "ifm_behaviors",
                    "repository": "IFM/Pretrain-Behaviors",
                    "revision": IFM_LIVE_REVISION,
                    "source_file": IFM_LIVE_FILE,
                    "row_index": row_index,
                    "selection_hash": plan_hash,
                },
            },
            ensure_ascii=False,
        )
        for row_index, row in enumerate(live_rows)
    ]
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_dir = tmp_path / "canonical"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "ifm_general",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    docs = [
        json.loads(line)
        for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(docs) == 2
    assert docs[0]["text"] == "First complete training document."
    assert "Instruction:" not in docs[0]["text"]
    assert docs[0]["source_metadata"]["upstream_token_count"] == 7
    assert docs[0]["source_metadata"]["subset"] == "general"
    assert docs[0]["language"] == "en"
    assert docs[0]["source_revision"] == IFM_LIVE_REVISION
    assert docs[1]["source_row"] == 1


IFM_PLANNING_REVISION = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"
IFM_PLANNING_FILE = "planning/planning.chunk0-160f3594ed-00416.parquet"


def test_data_adapt_ifm_planning_live_shape(tmp_path: Path) -> None:
    """The existing seam adapts live-shaped planning {text, token_count} rows."""
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        PlanAuthorization,
        load_acquisition_plan,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_adapt_ifm_planning_fixture",
        source_id="ifm_behaviors",
        view_id="planning",
        provider="huggingface",
        repository="IFM/Pretrain-Behaviors",
        revision=IFM_PLANNING_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[IFM_PLANNING_FILE],
        row_ranges={IFM_PLANNING_FILE: (0, 3)},
        output_artifact_id="raw_ifm_behaviors_planning",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=32 * 1024**2, max_records=100),
        authorization=PlanAuthorization(
            authorization_hash="",
            authorized_by="closeout-fixture",
            authorized_at="2026-09-21T00:00:00Z",
            scope="pilot",
            is_pilot_approved=True,
        ),
    )
    authorized = plan.model_copy(
        update={
            "authorization": plan.authorization.model_copy(
                update={"authorization_hash": plan.compute_behavioral_hash()}
            )
        }
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(authorized, plan_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()

    live_rows = [
        {"text": "First complete planning document.", "token_count": 11},
        {"text": "Second complete planning document.", "token_count": 13},
    ]
    lines = [
        json.dumps(
            {
                **row,
                "_xlm_acquisition": {
                    "source_id": "ifm_behaviors",
                    "repository": "IFM/Pretrain-Behaviors",
                    "revision": IFM_PLANNING_REVISION,
                    "source_file": IFM_PLANNING_FILE,
                    "row_index": row_index,
                    "selection_hash": plan_hash,
                },
            },
            ensure_ascii=False,
        )
        for row_index, row in enumerate(live_rows)
    ]
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_dir = tmp_path / "canonical"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "ifm_planning",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    docs = [
        json.loads(line)
        for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(docs) == 2
    assert docs[0]["text"] == "First complete planning document."
    assert "Goal:" not in docs[0]["text"]
    assert docs[0]["source_metadata"]["upstream_token_count"] == 11
    assert docs[0]["source_metadata"]["subset"] == "planning"
    assert docs[0]["doc_id"] == canonical_source_doc_id(
        "ifm_behaviors:planning", IFM_PLANNING_FILE, 0
    )
    assert docs[0]["language"] == "en"
    assert docs[0]["source_revision"] == IFM_PLANNING_REVISION
    assert docs[0]["source_file"] == IFM_PLANNING_FILE
    assert docs[1]["source_row"] == 1
