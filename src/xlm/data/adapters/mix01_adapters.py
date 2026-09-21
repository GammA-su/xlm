"""Explicit per-family adapters for the XLM-Mix-01 views.

Each adapter converts one upstream family's records into :class:`CanonicalDocument`
using only probed or registry-declared field names. Conceptual category names are
never assumed to be upstream fields: every required field is checked and a missing
field raises instead of passing silently. Filtering (non-English rows, synthetic
rows inside organic views, reasoning traces inside the explanations treatment,
RolmOCR image-OCR rows inside the Docling text-extracted PDF view) raises an
explicit rejection with a reason; nothing is dropped quietly.

The adapters are tested against authored schema fixtures shaped like the expected
upstream records. Live schemas are unverified, so these adapters carry the
registry's ``live_verified=False`` status until a real pilot tests them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.sources.schema import RowExtractorContract

ESSENTIAL_TAXONOMY_COMPONENTS = {
    "science": "essential_science",
    "explanation": "essential_science",
    "practical": "essential_practical",
    "procedure": "essential_practical",
    "howto": "essential_practical",
    "prose": "essential_prose",
    "general": "essential_prose",
}

NEMOTRON_ORGANIC_CATEGORIES = ("High-Quality", "Medium-High-Quality")


class AdapterError(ValueError):
    """Base error for mix01 adapter failures."""


class MissingFieldError(AdapterError):
    """A required upstream field is absent: the record cannot pass silently."""


class RecordRejectedError(AdapterError):
    """The record is well-formed but excluded by the view policy, with a reason."""


def _require(record: Mapping[str, Any], field_name: str, adapter_id: str) -> Any:
    """Fetch a required field, failing closed on absence, null or blank text."""
    if field_name not in record or record[field_name] is None:
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{field_name}'; "
            "it is absent and cannot be guessed."
        )
    value = record[field_name]
    if isinstance(value, str) and not value.strip():
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires non-empty upstream field '{field_name}'."
        )
    return value


def _require_english(language: str, allowed: tuple[str, ...], adapter_id: str) -> None:
    if language not in allowed:
        raise RecordRejectedError(
            f"adapter '{adapter_id}' keeps {allowed} rows only; row declares language '{language}'."
        )


def _optional_upstream_token_count(record: Mapping[str, Any], adapter_id: str) -> int | None:
    """Shared declared-``token_count`` contract: optional upstream metadata only.

    When present it must be a non-negative integer and is returned for
    preservation in ``source_metadata``. It never replaces XLM's own tokenizer
    counts. Anything malformed is refused, never coerced.
    """
    if "token_count" not in record or record["token_count"] is None:
        return None
    token_count = record["token_count"]
    if not isinstance(token_count, int) or isinstance(token_count, bool) or token_count < 0:
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field "
            "'token_count' to be a non-negative integer when present."
        )
    return token_count


def _ifm_text_and_token_count(record: Mapping[str, Any], adapter_id: str) -> tuple[str, int | None]:
    """Shared IFM prose contract: verbatim string ``text`` plus declared metadata.

    ``token_count`` is optional; when present it must be a non-negative integer
    and is returned for preservation in ``source_metadata``. It never replaces
    XLM's own tokenizer counts. Anything malformed is refused, never coerced.
    """
    text = _require(record, "text", adapter_id)
    if not isinstance(text, str):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field 'text' "
            "to be a string; it cannot be coerced."
        )
    return text, _optional_upstream_token_count(record, adapter_id)


def _ifm_source_metadata(subset: str, token_count: int | None) -> dict[str, Any]:
    """Shared IFM lineage: component, subset, and view-level language provenance.

    Rows carry no language field. ``language="en"`` holds because the mix01
    registry defines this exact view/config as English-only (component
    ``ifm_behaviors_general_planning``); that provenance is recorded here
    instead of per-record detection, which is never fabricated.
    """
    metadata: dict[str, Any] = {
        "mix01_component": "ifm_behaviors_general_planning",
        "subset": subset,
        "language_provenance": "view English-only (mix01 registry)",
    }
    if token_count is not None:
        metadata["upstream_token_count"] = token_count
    return metadata


def _canonical_doc(
    *,
    doc_id: str,
    source_id: str,
    source_revision: str,
    source_file: str,
    source_row: int,
    text: str,
    language: str,
    document_kind: str,
    license_reference: str,
    source_metadata: dict[str, Any],
    split: str = "train",
) -> CanonicalDocument:
    raw = text.encode("utf-8")
    digest = compute_sha256(raw)
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision=source_revision,
        source_file=source_file,
        source_row=source_row,
        raw_hash=digest,
        clean_hash=digest,
        text=text,
        utf8_byte_count=len(raw),
        language=language,
        language_confidence=1.0,
        document_kind=document_kind,
        source_metadata=source_metadata,
        parent_ids=[],
        license_reference=license_reference,
        transform_log=[{"adapter": "mix01", "action": "render"}],
        quality_reasons=[],
        cluster_ids={},
        split=split,
    )


class EssentialWebAdapter:
    """Essential-Web taxonomy/quality slices. Fail-closed fallback policy.

    Missing ``taxonomy`` or ``quality_tier`` metadata is REJECTED, never defaulted:
    defaulting would silently move a document into a treatment slice it was never
    selected for. An unrecognized taxonomy value is rejected for the same reason.
    """

    ADAPTER_ID = "essential_web"
    REQUIRED_FIELDS = ("text", "taxonomy", "quality_tier", "language")

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text", "taxonomy", "quality_tier", "language"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = str(_require(record, "text", self.ADAPTER_ID))
        taxonomy = str(_require(record, "taxonomy", self.ADAPTER_ID))
        quality = str(_require(record, "quality_tier", self.ADAPTER_ID))
        language = str(_require(record, "language", self.ADAPTER_ID))
        _require_english(language, ("en",), self.ADAPTER_ID)

        component = ESSENTIAL_TAXONOMY_COMPONENTS.get(taxonomy)
        if component is None:
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' does not recognize taxonomy '{taxonomy}'; "
                f"known taxonomies: {sorted(ESSENTIAL_TAXONOMY_COMPONENTS)}."
            )
        return _canonical_doc(
            doc_id=f"essential_web:{source_row}",
            source_id="essential_web",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="unknown",
            source_metadata={
                "mix01_component": component,
                "taxonomy": taxonomy,
                "quality_tier": quality,
            },
        )


class NemotronOrganicAdapter:
    """One organic Nemotron-CC category, selected by exact config name.

    Only the configured organic category passes. ``*-Synthetic`` rows and any
    other category are rejected explicitly, which is what keeps the organic and
    synthetic treatments separate even though they share a repository.
    """

    ADAPTER_ID = "nemotron_organic"
    REQUIRED_FIELDS = ("text", "quality_category")

    def __init__(self, config_name: str) -> None:
        if config_name not in NEMOTRON_ORGANIC_CATEGORIES:
            raise ValueError(
                f"organic adapter must select an exact organic category "
                f"{list(NEMOTRON_ORGANIC_CATEGORIES)}, got '{config_name}'."
            )
        self.config_name = config_name

    @property
    def component_id(self) -> str:
        return (
            "nemotron_organic_high"
            if self.config_name == "High-Quality"
            else "nemotron_organic_medium_high"
        )

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text", "quality_category"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = str(_require(record, "text", self.ADAPTER_ID))
        category = str(_require(record, "quality_category", self.ADAPTER_ID))
        if category != self.config_name:
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' selects '{self.config_name}' only; "
                f"row carries category '{category}'."
            )
        return _canonical_doc(
            doc_id=f"nemotron_cc21:{self.config_name}:{source_row}",
            source_id="nemotron_cc21",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="other",
            source_metadata={
                "mix01_component": self.component_id,
                "quality_category": category,
                "config_name": self.config_name,
            },
        )


class SynthExplanationsAdapter:
    """English SYNTH explanations with required context.

    The treatment renders necessary context, question and final explanation.
    Reasoning traces are a separate treatment: a record carrying one is rejected
    here rather than silently stripped. Missing context fails the record.
    """

    ADAPTER_ID = "synth_en"
    REQUIRED_FIELDS = ("context", "question", "explanation", "language")

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            required_fields=["context", "question", "explanation", "language"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        context = str(_require(record, "context", self.ADAPTER_ID))
        question = str(_require(record, "question", self.ADAPTER_ID))
        explanation = str(_require(record, "explanation", self.ADAPTER_ID))
        language = str(_require(record, "language", self.ADAPTER_ID))
        _require_english(language, ("en",), self.ADAPTER_ID)

        trace = record.get("reasoning_trace")
        if isinstance(trace, str) and trace.strip():
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' renders explanations, not reasoning "
                "traces; traces are a separate treatment."
            )
        text = (
            f"Context: {context.strip()}\nQuestion: {question.strip()}"
            f"\nExplanation: {explanation.strip()}"
        )
        return _canonical_doc(
            doc_id=f"synth:{source_row}",
            source_id="synth",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="cc-by-4.0",
            source_metadata={
                "mix01_component": "synth_en_explanations",
                "has_context": True,
                "config_name": "default",
            },
        )


class WikiRewriteAdapter:
    """The specialized Wiki-Rewrite component only."""

    ADAPTER_ID = "wiki_rewrite"
    REQUIRED_FIELDS = ("text",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = str(_require(record, "text", self.ADAPTER_ID))
        return _canonical_doc(
            doc_id=f"wiki_rewrite:{source_row}",
            source_id="nemotron_specialized",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="cc-by-4.0",
            source_metadata={
                "mix01_component": "nemotron_wiki_rewrite",
                "config_name": "Nemotron-Pretraining-Wiki-Rewrite",
            },
        )


class FineWikiAdapter:
    """English Wikipedia article prose (config ``en`` only).

    Live schema (pinned revision ``8bd13e72e6a002407649b3e898535f42ceb1aeb9``,
    view ``en``): records carry ``title``, ``text``, and ``in_language`` —
    there is no ``language`` field, and it must not be invented. The upstream
    ``text`` is the complete Markdown article whose first line is already the
    ``# {title}`` heading, so the adapter keeps it verbatim instead of
    prepending the title a second time. A record whose first line is not
    exactly that heading keeps the legacy ``"{title}\\n\\n{text}"`` rendering.
    """

    ADAPTER_ID = "finewiki_en"
    REQUIRED_FIELDS = ("title", "text", "in_language")

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["title", "text", "in_language"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        title = str(_require(record, "title", self.ADAPTER_ID))
        text = str(_require(record, "text", self.ADAPTER_ID))
        language = str(_require(record, "in_language", self.ADAPTER_ID))
        _require_english(language, ("en",), self.ADAPTER_ID)
        stripped_title = title.strip()
        stripped_text = text.strip()
        if stripped_text.split("\n", 1)[0].strip() == f"# {stripped_title}":
            rendered = stripped_text
        else:
            rendered = f"{stripped_title}\n\n{stripped_text}"
        return _canonical_doc(
            doc_id=f"finewiki:{source_row}",
            source_id="finewiki",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=rendered,
            language="en",
            document_kind="prose",
            license_reference="cc-by-sa-4.0",
            source_metadata={
                "mix01_component": "finewiki_en",
                "config_name": "en",
            },
        )


FINEPDFS_TEXT_EXTRACTOR = "docling"
FINEPDFS_IMAGE_OCR_EXTRACTOR = "rolmOCR"
FINEPDFS_KNOWN_EXTRACTORS = (FINEPDFS_TEXT_EXTRACTOR, FINEPDFS_IMAGE_OCR_EXTRACTOR)


def _finepdfs_extractor(record: Mapping[str, Any], adapter_id: str) -> str:
    """Enforce the real upstream extraction-method contract.

    The live schema carries no ``ocr_fallback`` field; the extraction method
    is the ``extractor`` string. Only ``docling`` (Docling/PyMuPDF
    text-extracted PDF content) passes this treatment. ``rolmOCR`` (RolmOCR
    image-based OCR extraction) is an explicit source-policy rejection, and
    any other value fails closed rather than passing silently. Matching is
    exact: near-miss spellings are not guessed.
    """
    extractor = _require(record, "extractor", adapter_id)
    if not isinstance(extractor, str):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field 'extractor' "
            "to be a string; it cannot be coerced."
        )
    if extractor == FINEPDFS_IMAGE_OCR_EXTRACTOR:
        raise RecordRejectedError(
            f"adapter '{adapter_id}' keeps Docling/PyMuPDF text-extracted PDF "
            "content only; this row carries extractor "
            f"'{FINEPDFS_IMAGE_OCR_EXTRACTOR}' (RolmOCR image-based OCR "
            "extraction), which is a separate treatment."
        )
    if extractor != FINEPDFS_TEXT_EXTRACTOR:
        raise RecordRejectedError(
            f"adapter '{adapter_id}' knows extractors "
            f"{sorted(FINEPDFS_KNOWN_EXTRACTORS)}; row carries extractor "
            f"'{extractor}'."
        )
    return extractor


def _finepdfs_optional_scalar(
    record: Mapping[str, Any], field_name: str, adapter_id: str, *, kind: str
) -> str | float | None:
    """Preserve one bounded upstream scalar annotation, or refuse it.

    ``kind`` is ``"label"`` (non-empty string, e.g. LID codes) or ``"score"``
    (int/float, never bool). Absent or null fields are skipped; malformed
    values are refused, never coerced. Page-level arrays are never copied
    here: bounded scalar metadata only, the raw record stays upstream.
    """
    if field_name not in record or record[field_name] is None:
        return None
    value = record[field_name]
    if kind == "label":
        if not isinstance(value, str) or not value.strip():
            raise MissingFieldError(
                f"adapter '{adapter_id}' requires upstream field '{field_name}' "
                "to be a non-empty string when present."
            )
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{field_name}' "
            "to be a number when present."
        )
    return value


class FinePdfsAdapter:
    """English-routed educational PDF text (config ``eng_Latn`` only).

    Live schema (pinned revision ``9cfabe2127faca99b3d5c4dc6d1fcb397399ebde``,
    config ``eng_Latn``): records carry ``text``, ``language`` and
    ``extractor``. There is no ``ocr_fallback`` field, and none is invented:
    the extraction method is the ``extractor`` string. Only ``docling``
    (Docling/PyMuPDF text-extracted PDF content) passes this treatment;
    ``rolmOCR`` (RolmOCR image-based OCR extraction) is an explicit
    source-policy rejection, and unknown extractor values fail closed.

    ``language == "eng_Latn"`` is the upstream config/routing label, not a
    monolingual purity guarantee: live rows may open in another language and
    mix languages per page. Canonical ``language`` stays ``en`` for rows
    routed through this view, but ``source_metadata["language_provenance"]``
    records the routing-label basis, and downstream language cleaning — not
    this adapter — makes the text-quality/language decision. No LID
    threshold is applied here.

    Accepted ``text`` is preserved verbatim: no headings, prefixes, table
    rewrites or OCR correction. Bounded scalar annotations (``is_truncated``,
    ``token_count`` as ``upstream_token_count``, document/page-average LID
    codes and scores) are preserved; page-level arrays (``per_page_languages``,
    ``page_ends``, ``fw_edu_scores``) are intentionally not copied into
    canonical metadata — the raw acquisition record retains them.
    ``token_count`` is declared upstream metadata only and never replaces
    XLM's own tokenizer accounting.

    Document identity incorporates the source file stem
    (``finepdfs:{stem}:{row}``): acquisition ``row_index`` locators are
    file-local, so a bare row number would collide across the many Parquet
    shards. The file stem is the upstream shard identity from the plan
    locator, so IDs stay deterministic across reruns.
    """

    ADAPTER_ID = "finepdfs_en"
    REQUIRED_FIELDS = ("text", "language", "extractor")

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text", "language", "extractor"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = _require(record, "text", self.ADAPTER_ID)
        if not isinstance(text, str):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'text' "
                "to be a string; it cannot be coerced."
            )
        language = _require(record, "language", self.ADAPTER_ID)
        if not isinstance(language, str):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'language' "
                "to be a string; it cannot be coerced."
            )
        _require_english(language, ("eng_Latn",), self.ADAPTER_ID)
        extractor = _finepdfs_extractor(record, self.ADAPTER_ID)

        source_metadata: dict[str, Any] = {
            "mix01_component": "finepdfs_en",
            "config_name": "eng_Latn",
            "extractor": extractor,
            "language_provenance": (
                "upstream routing label eng_Latn (mix01 registry); "
                "not a monolingual purity guarantee — "
                "downstream language cleaning decides"
            ),
        }
        if "is_truncated" in record and record["is_truncated"] is not None:
            if not isinstance(record["is_truncated"], bool):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field "
                    "'is_truncated' to be a boolean when present."
                )
            source_metadata["is_truncated"] = record["is_truncated"]
        token_count = _optional_upstream_token_count(record, self.ADAPTER_ID)
        if token_count is not None:
            source_metadata["upstream_token_count"] = token_count
        for field_name, metadata_key, kind in (
            ("full_doc_lid", "upstream_full_doc_lid", "label"),
            ("full_doc_lid_score", "upstream_full_doc_lid_score", "score"),
            ("page_average_lid", "upstream_page_average_lid", "label"),
            ("page_average_lid_score", "upstream_page_average_lid_score", "score"),
        ):
            scalar = _finepdfs_optional_scalar(record, field_name, self.ADAPTER_ID, kind=kind)
            if scalar is not None:
                source_metadata[metadata_key] = scalar

        return _canonical_doc(
            doc_id=f"finepdfs:{Path(source_file).stem}:{source_row}",
            source_id="finepdfs_edu",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="odc-by",
            source_metadata=source_metadata,
        )


class IfmGeneralAdapter:
    """IFM Pretrain-Behaviors ``general`` subset: complete training prose.

    Live schema (pinned revision ``3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5``,
    config ``general``): records carry ``text`` — the complete formatted
    pretraining document, preserved verbatim — and an optional declared
    ``token_count``. There are no ``instruction``/``response`` fields, and
    none are inferred; no ``Instruction:``/``Response:`` prefixes are added.
    ``token_count`` is preserved in ``source_metadata`` as a declared
    upstream value only; it never replaces XLM's own tokenizer counts.
    Rows carry no language field. ``language="en"`` holds because the mix01
    registry defines this exact view/config as English-only (component
    ``ifm_behaviors_general_planning``); that provenance is recorded in
    ``source_metadata["language_provenance"]`` instead of per-record
    detection, which is never fabricated.
    """

    ADAPTER_ID = "ifm_general"
    REQUIRED_FIELDS = ("text",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text, token_count = _ifm_text_and_token_count(record, self.ADAPTER_ID)
        return _canonical_doc(
            doc_id=f"ifm_behaviors:general:{source_row}",
            source_id="ifm_behaviors",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="apache-2.0",
            source_metadata=_ifm_source_metadata("general", token_count),
        )


class IfmPlanningAdapter:
    """IFM Pretrain-Behaviors ``planning`` subset: complete training prose.

    Live schema (pinned revision ``3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5``,
    config ``planning``): records carry ``text`` — the complete formatted
    pretraining document, preserved verbatim — and an optional declared
    ``token_count``. There are no ``goal``/``plan_steps`` fields, and none
    are inferred; no ``Goal:``/``Plan:`` formatting is invented and no
    structured fields are recovered from prose. ``token_count`` and
    ``language="en"`` follow the same contract as the ``general`` subset:
    declared upstream metadata only, and view-level English-only provenance
    from the mix01 registry (component ``ifm_behaviors_general_planning``,
    whose ``config_names`` cover ``planning``).
    """

    ADAPTER_ID = "ifm_planning"
    REQUIRED_FIELDS = ("text",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text, token_count = _ifm_text_and_token_count(record, self.ADAPTER_ID)
        return _canonical_doc(
            doc_id=f"ifm_behaviors:planning:{source_row}",
            source_id="ifm_behaviors",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="apache-2.0",
            source_metadata=_ifm_source_metadata("planning", token_count),
        )


class CommonPileAdapter:
    """Selected complementary Common Pile prose."""

    ADAPTER_ID = "common_pile"
    REQUIRED_FIELDS = ("text",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = str(_require(record, "text", self.ADAPTER_ID))
        return _canonical_doc(
            doc_id=f"common_pile:{source_row}",
            source_id="common_pile",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="unknown",
            source_metadata={"mix01_component": "common_pile_prose"},
        )


class SimpleStoriesAdapter:
    """Small coherent-story component."""

    ADAPTER_ID = "simple_stories"
    REQUIRED_FIELDS = ("story",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            required_fields=["story"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        story = str(_require(record, "story", self.ADAPTER_ID))
        return _canonical_doc(
            doc_id=f"simple_stories:{source_row}",
            source_id="simple_stories",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=story,
            language="en",
            document_kind="prose",
            license_reference="mit",
            source_metadata={"mix01_component": "simple_stories"},
        )


class Txt360WebAdapter:
    """TxT360 web view for the M4 treatment (``web-high-medium`` only)."""

    ADAPTER_ID = "txt360_web"
    REQUIRED_FIELDS = ("text", "quality_band")

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text", "quality_band"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = str(_require(record, "text", self.ADAPTER_ID))
        band = str(_require(record, "quality_band", self.ADAPTER_ID))
        if band != "web-high-medium":
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' selects the 'web-high-medium' band only; "
                f"row carries band '{band}'."
            )
        return _canonical_doc(
            doc_id=f"txt360:{source_row}",
            source_id="txt360_v2",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="cc-by-4.0",
            source_metadata={
                "mix01_component": "txt360_web",
                "config_name": "web-high-medium",
            },
        )


ADAPTERS_BY_ID = {
    EssentialWebAdapter.ADAPTER_ID: EssentialWebAdapter,
    NemotronOrganicAdapter.ADAPTER_ID: NemotronOrganicAdapter,
    SynthExplanationsAdapter.ADAPTER_ID: SynthExplanationsAdapter,
    WikiRewriteAdapter.ADAPTER_ID: WikiRewriteAdapter,
    FineWikiAdapter.ADAPTER_ID: FineWikiAdapter,
    FinePdfsAdapter.ADAPTER_ID: FinePdfsAdapter,
    IfmGeneralAdapter.ADAPTER_ID: IfmGeneralAdapter,
    IfmPlanningAdapter.ADAPTER_ID: IfmPlanningAdapter,
    CommonPileAdapter.ADAPTER_ID: CommonPileAdapter,
    SimpleStoriesAdapter.ADAPTER_ID: SimpleStoriesAdapter,
    Txt360WebAdapter.ADAPTER_ID: Txt360WebAdapter,
}
