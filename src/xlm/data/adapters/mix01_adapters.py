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
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters.source_ids import canonical_source_doc_id
from xlm.data.normalization import compute_sha256
from xlm.data.sources.schema import RowExtractorContract

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


def _require_recordable_content_text(
    record: Mapping[str, Any], field_name: str, adapter_id: str
) -> str:
    """Fetch a required training-content string; unusable content is a policy drop.

    The column must exist and hold a string (schema: fatal otherwise). A
    present-but-null or all-whitespace value is row-level content quality,
    so it raises :class:`RecordRejectedError` for ``--on-reject record``.
    Valid text is returned verbatim, never stripped.
    """
    if field_name not in record:
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{field_name}'; "
            "it is absent and cannot be guessed."
        )
    value = record[field_name]
    if value is None:
        raise RecordRejectedError(
            f"adapter '{adapter_id}' drops rows without usable '{field_name}' content (null)."
        )
    if not isinstance(value, str):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{field_name}' "
            "to be a string; it cannot be coerced."
        )
    if not value.strip():
        raise RecordRejectedError(
            f"adapter '{adapter_id}' drops rows without usable '{field_name}' content "
            "(empty or whitespace-only)."
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


ESSENTIAL_WEB_COMPONENTS = (
    "essential_science",
    "essential_practical",
    "essential_prose",
)


def _require_mapping(
    record: Mapping[str, Any], field_name: str, adapter_id: str
) -> Mapping[str, Any]:
    """Fetch a required nested mapping, failing closed on absence or wrong type."""
    value = _require(record, field_name, adapter_id)
    if not isinstance(value, Mapping):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{field_name}' "
            f"to be a mapping, got {type(value).__name__}; it cannot be coerced."
        )
    return value


def _optional_nonempty_str(mapping: Mapping[str, Any], key: str, adapter_id: str) -> str | None:
    """Keep a bounded scalar string, or refuse it when malformed (never coerce)."""
    if key not in mapping or mapping[key] is None:
        return None
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{key}' "
            "to be a non-empty string when present."
        )
    return value


def _optional_number(mapping: Mapping[str, Any], key: str, adapter_id: str) -> int | float | None:
    """Keep a bounded scalar number, or refuse it when malformed (never coerce)."""
    if key not in mapping or mapping[key] is None:
        return None
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires upstream field '{key}' to be a number when present."
        )
    return value


def _classifier_primary_label(classifier: Mapping[str, Any], adapter_id: str) -> str | None:
    """Extract ``primary.label`` from one ``eai_taxonomy`` classifier, if well-formed."""
    if "primary" not in classifier or classifier["primary"] is None:
        return None
    primary = classifier["primary"]
    if not isinstance(primary, Mapping):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires classifier 'primary' "
            f"to be a mapping, got {type(primary).__name__}."
        )
    return _optional_nonempty_str(primary, "label", adapter_id)


def _classifier_primary_code(classifier: Mapping[str, Any], adapter_id: str) -> str | None:
    """Extract ``primary.code`` from one ``eai_taxonomy`` classifier, if well-formed."""
    if "primary" not in classifier or classifier["primary"] is None:
        return None
    primary = classifier["primary"]
    if not isinstance(primary, Mapping):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires classifier 'primary' "
            f"to be a mapping, got {type(primary).__name__}."
        )
    code = primary.get("code")
    if code is None:
        return None
    if isinstance(code, bool) or not isinstance(code, (int, float, str)):
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires classifier 'primary.code' "
            "to be a string or number when present."
        )
    text = str(code).strip()
    if not text:
        raise MissingFieldError(
            f"adapter '{adapter_id}' requires classifier 'primary.code' "
            "to be non-empty when present."
        )
    return text


class EssentialWebAdapter:
    """English-filtered Essential-Web rows (``EssentialAI/essential-web-v1.0``).

    Live schema (pinned revision
    ``ce4eccc7e9604667b6d7f32cb6274b8b41f3113d``): records carry ``text``,
    ``eai_taxonomy`` (structured classifiers such as
    ``free_decimal_correspondence``,
    ``document_type_v2``, ``bloom_knowledge_domain``), ``quality_signals``
    (``fasttext`` scores including ``english``, plus ``red_pajama_v2``),
    integer ``id``, string ``pid``, and ``metadata``. There are no flat
    ``taxonomy`` / ``quality_tier`` / ``language`` fields, and none are
    invented: the old flat-taxonomy contract never matched this source.

    Slice assignment is explicit operator configuration, not inference: the
    constructor takes one of ``essential_science`` / ``essential_practical`` /
    ``essential_prose`` and records it as ``mix01_component``. Mapping nested
    classifier labels onto slices without a documented selection policy would
    be mixture-policy invention, so this adapter never does it. Science /
    practical / prose subset thresholds remain a separate explicit policy
    decision.

    Canonical ``language`` is ``en`` because this treatment is the upstream
    English-filtered view; provenance records that basis truthfully and the
    upstream fastText English confidence is preserved as bounded metadata.
    No English-score threshold is applied. Upstream ``text`` is preserved
    verbatim.
    """

    ADAPTER_ID = "essential_web"
    REQUIRED_FIELDS = ("text", "eai_taxonomy", "quality_signals", "id", "pid", "metadata")

    def __init__(self, component: str) -> None:
        if component not in ESSENTIAL_WEB_COMPONENTS:
            raise ValueError(
                "essential_web slice assignment is explicit operator configuration; "
                f"component must be one of {list(ESSENTIAL_WEB_COMPONENTS)}, "
                f"got '{component}'."
            )
        self.component = component

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=[
                "text",
                "eai_taxonomy",
                "quality_signals",
                "id",
                "pid",
                "metadata",
            ],
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
        if not isinstance(text, str) or not text:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'text' "
                "to be a non-empty string; it is preserved verbatim, never coerced."
            )
        taxonomy = _require_mapping(record, "eai_taxonomy", self.ADAPTER_ID)
        quality = _require_mapping(record, "quality_signals", self.ADAPTER_ID)
        fasttext = quality.get("fasttext")
        if not isinstance(fasttext, Mapping):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'quality_signals.fasttext' to be a mapping."
            )
        english_score = _optional_number(dict(fasttext), "english", self.ADAPTER_ID)
        if english_score is None:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'quality_signals.fasttext.english' to be a number."
            )
        upstream_id = _require(record, "id", self.ADAPTER_ID)
        if isinstance(upstream_id, bool) or not isinstance(upstream_id, int):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'id' "
                "to be an integer document identifier."
            )
        pid = _require(record, "pid", self.ADAPTER_ID)
        if not isinstance(pid, str) or not pid.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'pid' "
                "to be a non-empty string."
            )
        source_metadata_block = _require_mapping(record, "metadata", self.ADAPTER_ID)

        source_metadata: dict[str, Any] = {
            "mix01_component": self.component,
            "language_provenance": (
                "upstream Essential-Web English-filtered treatment; "
                "fastText English confidence preserved as fasttext_english; "
                "downstream language cleaning still decides"
            ),
            "upstream_id": upstream_id,
            "pid": pid,
            "fasttext_english": english_score,
        }
        for key, metadata_key in (
            ("source_domain", "source_domain"),
            ("snapshot_id", "snapshot_id"),
        ):
            value = _optional_nonempty_str(source_metadata_block, key, self.ADAPTER_ID)
            if value is not None:
                source_metadata[metadata_key] = value
        for key, metadata_key in (
            ("fineweb_edu_approx", "fasttext_fineweb_edu_approx"),
            ("dclm", "fasttext_dclm"),
            ("eai_general_math", "fasttext_eai_general_math"),
            ("eai_open_web_math", "fasttext_eai_open_web_math"),
            ("eai_web_code", "fasttext_eai_web_code"),
        ):
            score = _optional_number(dict(fasttext), key, self.ADAPTER_ID)
            if score is not None:
                source_metadata[metadata_key] = score
        fdc = taxonomy.get("free_decimal_correspondence")
        if isinstance(fdc, Mapping):
            code = _classifier_primary_code(fdc, self.ADAPTER_ID)
            if code is not None:
                source_metadata["fdc_primary_code"] = code
            primary = fdc.get("primary")
            labels = (
                primary.get("labels")
                if isinstance(primary, Mapping) and isinstance(primary.get("labels"), Mapping)
                else None
            )
            if labels is not None:
                for level in ("level_1", "level_2", "level_3"):
                    value = _optional_nonempty_str(labels, level, self.ADAPTER_ID)
                    if value is not None:
                        source_metadata[f"fdc_{level}"] = value
        elif fdc is not None:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires "
                "'eai_taxonomy.free_decimal_correspondence' to be a mapping when present."
            )
        for classifier_key, metadata_key in (
            ("document_type_v2", "document_type_v2_primary"),
            ("bloom_knowledge_domain", "bloom_knowledge_domain_primary"),
            ("bloom_cognitive_process", "bloom_cognitive_process_primary"),
        ):
            classifier = taxonomy.get(classifier_key)
            if classifier is None:
                continue
            if not isinstance(classifier, Mapping):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires "
                    f"'eai_taxonomy.{classifier_key}' to be a mapping when present."
                )
            label = _classifier_primary_label(classifier, self.ADAPTER_ID)
            if label is not None:
                source_metadata[metadata_key] = label
        return _canonical_doc(
            doc_id=canonical_source_doc_id("essential_web", source_file, source_row),
            source_id="essential_web",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="odc-by",
            source_metadata=source_metadata,
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
            doc_id=canonical_source_doc_id(
                f"nemotron_cc21:{self.config_name}", source_file, source_row
            ),
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


ULTRAX_REPOSITORY_CANDIDATES = ("openbmb/UltraX-Preview", "openbmb/UltraX")
ULTRAX_CONFIG_NAME = "UltraX-Ultra-FineWeb"


class UltraXUltraFineWebAdapter:
    """UltraX Ultra-FineWeb English web text (config ``UltraX-Ultra-FineWeb`` only).

    Published record shape (dataset card): ``uid`` (string, stable source
    document identity), ``raw_content`` (string, pre-refinement text, never
    training input), ``cleaned_content`` (string, refined training text),
    ``processed_functions`` (string, refinement operations applied), and
    ``source`` (string, upstream source label). The source is English.

    Mapping:

    - document text is ``cleaned_content`` verbatim, never ``raw_content``;
    - stable source identity is ``uid`` (preserved in ``source_metadata``;
      canonical ``doc_id`` stays the centralized file+row scheme so IDs are
      stable across reruns even before uid uniqueness is proven by the
      operator probe);
    - provenance records the exact repository, revision, config, source file,
      row, ``uid`` and the row ``source`` label;
    - ``processed_functions`` is optional bounded metadata, preserved
      verbatim when present.

    ``remove_all`` (empty/whitespace ``cleaned_content``) is an explicit
    counted policy drop (:class:`RecordRejectedError`); falling back to
    ``raw_content`` would undo the refinement and is forbidden.
    ``raw_content`` is never read and never copied into canonical output.
    Missing ``cleaned_content`` or a wrong type fails closed
    (:class:`MissingFieldError`); ``uid`` must be a non-empty string and is
    never coerced or assumed to follow a narrower format than the probe
    proves.
    """

    ADAPTER_ID = "ultrax_ultrafineweb"
    SOURCE_ID = "ultrax_ultrafineweb"
    REQUIRED_FIELDS = ("uid", "cleaned_content", "source")
    CONFIG_NAME = ULTRAX_CONFIG_NAME

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="cleaned_content",
            required_fields=["uid", "cleaned_content", "source"],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        uid = _require(record, "uid", self.ADAPTER_ID)
        if not isinstance(uid, str) or not uid.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'uid' "
                "to be a non-empty string; it cannot be coerced or guessed."
            )
        if "cleaned_content" not in record or record["cleaned_content"] is None:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'cleaned_content'; it is absent and cannot be guessed."
            )
        cleaned = record["cleaned_content"]
        if not isinstance(cleaned, str):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'cleaned_content' to be a string; it cannot be coerced."
            )
        if not cleaned.strip():
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' drops empty cleaned_content "
                "(remove_all by UltraX); raw_content is never used as fallback."
            )
        upstream_source = _require(record, "source", self.ADAPTER_ID)
        if not isinstance(upstream_source, str) or not upstream_source.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'source' "
                "to be a non-empty string."
            )
        source_metadata: dict[str, Any] = {
            "mix01_component": "ultrax_ultrafineweb",
            "config_name": self.CONFIG_NAME,
            "uid": uid,
            "upstream_source": upstream_source,
            "language_provenance": (
                "UltraX-Ultra-FineWeb treatment is documented as English; "
                "no row-level language field exists; "
                "downstream language cleaning still applies"
            ),
            "license_provenance": (
                "Dataset-level license pending operator probe receipt; "
                "UltraX is derived from source corpora and users must check "
                "applicable source-dataset licenses."
            ),
        }
        if "processed_functions" in record and record["processed_functions"] is not None:
            functions = record["processed_functions"]
            if not isinstance(functions, str):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field "
                    "'processed_functions' to be a string when present."
                )
            source_metadata["processed_functions"] = functions
        return _canonical_doc(
            doc_id=canonical_source_doc_id("ultrax_ultrafineweb", source_file, source_row),
            source_id=self.SOURCE_ID,
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=cleaned,
            language="en",
            document_kind="prose",
            license_reference="unknown",
            source_metadata=source_metadata,
        )


class SynthExplanationsAdapter:
    """English SYNTH question-answer pairs with required source context.

    Live schema (``PleIAs/SYNTH`` @ pinned revision, config ``default``):
    records carry ``synth_id``, ``language``, ``query``, ``query_seed_text``
    (the necessary source context), ``synthetic_answer``, and ``seed_license``.
    The treatment renders exactly::

        Context: <query_seed_text>
        Question: <query>
        Answer: <synthetic_answer>

    with source strings preserved verbatim (never stripped). SYNTH is
    multilingual and is NOT English-pure; this treatment keeps only rows
    explicitly labelled ``en``.

    Row semantics (fatal = :class:`MissingFieldError`, aborts adaptation;
    policy drop = :class:`RecordRejectedError`, recorded by
    ``--on-reject record``):

    - ``synth_id`` / ``seed_license`` (identity/provenance): absent, null,
      blank or non-string is fatal.
    - ``query`` / ``query_seed_text`` / ``synthetic_answer`` (training
      content): the columns are required schema, so absent or non-string is
      fatal; a present null or blank/whitespace-only value is a policy drop
      (unusable row content, observed on real rows).
    - ``language``: absent or non-string is fatal; null/blank (unlabelled)
      and any label other than ``en`` are policy drops.
    - ``exercise`` / ``model`` / ``query_seed_url`` / ``additional_seed_url``
      (optional metadata): absent, null or blank is omitted from canonical
      metadata; a valid string is preserved verbatim; a non-string is fatal.
      ``words`` is optional; a non-integer is fatal.

    Every fatal check runs before any policy drop, so a malformed row is
    never hidden behind a recordable rejection. Content drops are reported
    before language drops.

    ``synthetic_reasoning`` is an upstream field present on every row of the
    observed shard; reasoning CONTENT is deliberately excluded from training
    text, but the row itself is never rejected merely because reasoning
    exists. There are no ``context`` / ``question`` / ``explanation`` /
    ``reasoning_trace`` fields, and none are invented.
    """

    ADAPTER_ID = "synth_en"
    REQUIRED_FIELDS = (
        "synth_id",
        "language",
        "query",
        "query_seed_text",
        "synthetic_answer",
        "seed_license",
    )

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            required_fields=[
                "synth_id",
                "language",
                "query",
                "query_seed_text",
                "synthetic_answer",
                "seed_license",
            ],
        )

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        synth_id = _require(record, "synth_id", self.ADAPTER_ID)
        if not isinstance(synth_id, str) or not synth_id.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'synth_id' "
                "to be a non-empty string; it cannot be coerced."
            )
        seed_license = _require(record, "seed_license", self.ADAPTER_ID)
        if not isinstance(seed_license, str) or not seed_license.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'seed_license' "
                "to be a non-empty string; it cannot be coerced."
            )
        # Content: schema faults stay fatal for every field; the first
        # unusable-content drop is held until all fatal checks have run.
        content: dict[str, str] = {}
        content_drop: RecordRejectedError | None = None
        for name in ("query", "query_seed_text", "synthetic_answer"):
            try:
                content[name] = _require_recordable_content_text(record, name, self.ADAPTER_ID)
            except RecordRejectedError as exc:
                content_drop = content_drop or exc
        if "language" not in record:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'language'; "
                "it is absent and cannot be guessed."
            )
        language = record["language"]
        if language is not None and not isinstance(language, str):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'language' "
                "to be a string when present."
            )
        source_metadata: dict[str, Any] = {
            "mix01_component": "synth_en_explanations",
            "config_name": "default",
            "synth_id": synth_id,
            "seed_license": seed_license,
            "reasoning_treatment": "excluded",
        }
        for key, metadata_key in (
            ("exercise", "exercise"),
            ("model", "model"),
            ("query_seed_url", "query_seed_url"),
            ("additional_seed_url", "additional_seed_url"),
        ):
            if key not in record or record[key] is None:
                continue
            value = record[key]
            if not isinstance(value, str):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field '{key}' "
                    "to be a string when present."
                )
            if not value.strip():
                continue
            source_metadata[metadata_key] = value
        if "words" in record and record["words"] is not None:
            words = record["words"]
            if isinstance(words, bool) or not isinstance(words, int):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field 'words' "
                    "to be an integer when present."
                )
            source_metadata["words"] = words
        # Policy drops, only after every fatal schema check above passed.
        if content_drop is not None:
            raise content_drop
        if language is None or not language.strip():
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' keeps explicit 'en' rows only; "
                "row carries no usable language label."
            )
        if language != "en":
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' keeps explicit 'en' rows only; "
                f"row carries language '{language}'."
            )
        text = (
            f"Context: {content['query_seed_text']}\nQuestion: {content['query']}\n"
            f"Answer: {content['synthetic_answer']}"
        )
        return _canonical_doc(
            doc_id=canonical_source_doc_id("synth", source_file, source_row),
            source_id="synth",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="cc-by-4.0",
            source_metadata=source_metadata,
        )


class WikiRewriteAdapter:
    """English Wikipedia rewrites (``Nemotron-Pretraining-Wiki-Rewrite`` only).

    Live schema (``nvidia/Nemotron-Pretraining-Specialized-v1`` @ pinned
    revision, 1M-row shard): records carry ``text``, ``license``,
    ``metadata`` (mapping with ``category`` and ``models_used``), and
    ``uuid``. There is no row-level ``language`` field.

    The adapter requires the exact component category
    ``Nemotron-Pretraining-Wiki-Rewrite``: a row from another Specialized-v1
    component sharing this general schema is a policy exclusion
    (``RecordRejectedError``), never silently absorbed. Upstream ``text`` is
    preserved verbatim — no stripping, prefixing, or coercion.

    Canonical ``language`` is ``en`` because this exact upstream component is
    documented as rewritten English Wikipedia; provenance records that basis
    truthfully. ``license_reference`` keeps the dataset-level license
    (``cc-by-4.0``) per XLM convention while the real row-level license is
    preserved separately as ``upstream_license`` metadata.
    """

    ADAPTER_ID = "wiki_rewrite"
    REQUIRED_FIELDS = ("text", "license", "metadata", "uuid")
    WIKI_REWRITE_CATEGORY = "Nemotron-Pretraining-Wiki-Rewrite"

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text", "license", "metadata", "uuid"],
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
        if not isinstance(text, str) or not text:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'text' "
                "to be a non-empty string; it is preserved verbatim, never coerced."
            )
        upstream_license = _require(record, "license", self.ADAPTER_ID)
        if not isinstance(upstream_license, str) or not upstream_license.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'license' "
                "to be a non-empty string."
            )
        metadata = _require(record, "metadata", self.ADAPTER_ID)
        if not isinstance(metadata, Mapping):
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'metadata' "
                f"to be a mapping, got {type(metadata).__name__}."
            )
        category = metadata.get("category")
        if category is None:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'metadata.category'; it is absent and cannot be guessed."
            )
        if not isinstance(category, str) or not category.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field "
                "'metadata.category' to be a non-empty string."
            )
        if category != self.WIKI_REWRITE_CATEGORY:
            raise RecordRejectedError(
                f"adapter '{self.ADAPTER_ID}' selects category "
                f"'{self.WIKI_REWRITE_CATEGORY}' only; row carries category '{category}'."
            )
        upstream_uuid = _require(record, "uuid", self.ADAPTER_ID)
        if not isinstance(upstream_uuid, str) or not upstream_uuid.strip():
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'uuid' "
                "to be a non-empty string."
            )
        source_metadata: dict[str, Any] = {
            "mix01_component": "nemotron_wiki_rewrite",
            "config_name": "Nemotron-Pretraining-Wiki-Rewrite",
            "upstream_uuid": upstream_uuid,
            "upstream_license": upstream_license,
            "category": category,
            "language_provenance": (
                "upstream Nemotron Wiki-Rewrite treatment is documented as "
                "rewritten English Wikipedia; no row-level language field "
                "exists; downstream language cleaning still applies"
            ),
        }
        models_used = metadata.get("models_used")
        if models_used is not None:
            if not isinstance(models_used, str) or not models_used.strip():
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field "
                    "'metadata.models_used' to be a non-empty string when present."
                )
            source_metadata["models_used"] = models_used
        return _canonical_doc(
            doc_id=canonical_source_doc_id("wiki_rewrite", source_file, source_row),
            source_id="nemotron_specialized",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="cc-by-4.0",
            source_metadata=source_metadata,
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
            doc_id=canonical_source_doc_id("finewiki", source_file, source_row),
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

    Document identity follows the centralized file-row convention
    (``finepdfs:v1:<file-key>:<row>`` via ``canonical_source_doc_id``):
    acquisition ``row_index`` locators are file-local, so a bare row number
    would collide across the many Parquet shards, and a bare file stem would
    collide across directories holding same-named shards. The file key is a
    stable digest of the full upstream relative path, so IDs stay
    deterministic across reruns.
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
            doc_id=canonical_source_doc_id("finepdfs", source_file, source_row),
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
            doc_id=canonical_source_doc_id("ifm_behaviors:general", source_file, source_row),
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
            doc_id=canonical_source_doc_id("ifm_behaviors:planning", source_file, source_row),
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
    """Normalized Common Pile prose rows (``text`` only, component from path).

    Pinned source (``common-pile/comma_v0.1_training_dataset`` @ observed
    revision): every inspected row is exactly ``{"text": str}`` across many
    independent top-level components (``news/…``, ``libretexts/…``,
    ``public_domain_review/…``, and 28 more). The row carries no component,
    language, or license field.

    The upstream component is the top-level source directory of the relative
    source file (``news/news.chunk.09.jsonl.gz`` → ``news``). A componentless
    or malformed path is refused fail-closed rather than assigned a guessed
    component. Which of the 31 components belong in the final Mix-01
    treatment — including weights and per-component licensing/admission — is
    a separate mixture-policy decision this adapter never makes.

    Canonical ``language`` is ``en`` because ``common_pile_prose`` is defined
    as an English-only downstream treatment; raw rows expose no language
    field and 9 samples prove nothing corpus-wide, so provenance records that
    basis truthfully and downstream language cleaning still decides. The
    observed repository license is null, so ``license_reference`` stays the
    truthful ``unknown`` placeholder with a provenance note. Upstream text is
    preserved verbatim.
    """

    ADAPTER_ID = "common_pile"
    REQUIRED_FIELDS = ("text",)

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="text",
            required_fields=["text"],
        )

    @staticmethod
    def upstream_component(source_file: str) -> str:
        """Derive the top-level Common Pile component from a relative path.

        Normalizes separators to ``/`` (matching the central file-key
        convention, so Windows vs POSIX spellings agree) and returns the
        first path segment. Refuses empty, componentless, or dot-segment
        paths fail-closed.
        """
        if not isinstance(source_file, str) or not source_file:
            raise MissingFieldError(
                "adapter 'common_pile' requires a non-empty source_file to "
                "derive the upstream component."
            )
        normalized = source_file.replace("\\", "/")
        raw_segments = normalized.split("/")
        if ".." in raw_segments:
            raise MissingFieldError(
                f"adapter 'common_pile' cannot derive an upstream component from "
                f"source_file {source_file!r}."
            )
        segments = [segment for segment in raw_segments if segment not in ("", ".")]
        if len(segments) < 2:
            raise MissingFieldError(
                f"adapter 'common_pile' cannot derive an upstream component from "
                f"componentless source_file {source_file!r}."
            )
        return segments[0]

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = _require(record, "text", self.ADAPTER_ID)
        if not isinstance(text, str) or not text:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'text' "
                "to be a non-empty string; it is preserved verbatim, never coerced."
            )
        component = self.upstream_component(source_file)
        return _canonical_doc(
            doc_id=canonical_source_doc_id("common_pile", source_file, source_row),
            source_id="common_pile",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=text,
            language="en",
            document_kind="prose",
            license_reference="unknown",
            source_metadata={
                "mix01_component": "common_pile_prose",
                "upstream_component": component,
                "language_provenance": (
                    "Common Pile raw rows expose no language field; this XLM "
                    "treatment is intended to select English prose externally; "
                    "downstream language cleaning still decides."
                ),
                "license_provenance": (
                    "No single repository-level or row-level license was observed; "
                    "component-level licensing requires separate admission review."
                ),
            },
        )


class SimpleStoriesAdapter:
    """English SimpleStories narratives (``SimpleStories/SimpleStories``).

    Live train/test schema (pinned revision): records carry ``story`` plus
    bounded scalar provenance (topic/theme/style/feature/grammar/persona
    strings, word/character/paragraph counts, readability statistics,
    completion counts, ``generation_id``, ``model``). The only semantically
    necessary content field is ``story``, which is required non-empty,
    preserved verbatim, and never ``str()``-coerced. Optional provenance is
    preserved only when well-formed (strings stay strings, numbers stay
    numbers — empty strings kept as valid upstream values); malformed
    optional values are refused, never silently stringified or skipped.
    Story-only rows remain supported. There is no row-level language field.

    Canonical ``language`` is ``en`` because this XLM treatment is defined as
    the English SimpleStories component; provenance records that basis
    truthfully and downstream language cleaning still applies.
    ``license_reference`` is the dataset-level MIT license from pinned
    repository metadata. ``generation_id``/``model`` are provenance only,
    never identity: canonical identity stays the centralized file+row scheme.
    """

    ADAPTER_ID = "simple_stories"
    REQUIRED_FIELDS = ("story",)

    #: Optional bounded string provenance keys (empty strings are valid values).
    OPTIONAL_STRING_FIELDS = (
        "topic",
        "theme",
        "style",
        "feature",
        "grammar",
        "persona",
        "initial_word_type",
        "initial_letter",
        "generation_id",
        "model",
    )

    #: Optional bounded numeric provenance keys (ints stay ints, floats stay floats).
    OPTIONAL_NUMERIC_FIELDS = (
        "word_count",
        "character_count",
        "num_paragraphs",
        "avg_word_length",
        "avg_sentence_length",
        "flesch_reading_ease",
        "flesch_kincaid_grade",
        "dale_chall_readability_score",
        "num_stories_in_completion",
        "expected_num_stories_in_completion",
    )

    def contract(self) -> RowExtractorContract:
        return RowExtractorContract(
            adapter_id=self.ADAPTER_ID,
            text_field="story",
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
        story = _require(record, "story", self.ADAPTER_ID)
        if not isinstance(story, str) or not story:
            raise MissingFieldError(
                f"adapter '{self.ADAPTER_ID}' requires upstream field 'story' "
                "to be a non-empty string; it is preserved verbatim, never coerced."
            )
        source_metadata: dict[str, Any] = {
            "mix01_component": "simple_stories",
            "language_provenance": (
                "SimpleStories treatment is English; no row-level language field "
                "exists in the certified schema; downstream language cleaning "
                "still applies"
            ),
            "license_provenance": "dataset-level MIT license from pinned repository metadata",
        }
        for key in self.OPTIONAL_STRING_FIELDS:
            if key not in record or record[key] is None:
                continue
            value = record[key]
            if not isinstance(value, str):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field '{key}' "
                    "to be a string when present."
                )
            source_metadata[key] = value
        for key in self.OPTIONAL_NUMERIC_FIELDS:
            if key not in record or record[key] is None:
                continue
            value = record[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MissingFieldError(
                    f"adapter '{self.ADAPTER_ID}' requires upstream field '{key}' "
                    "to be a number when present."
                )
            source_metadata[key] = value
        return _canonical_doc(
            doc_id=canonical_source_doc_id("simple_stories", source_file, source_row),
            source_id="simple_stories",
            source_revision=source_revision,
            source_file=source_file,
            source_row=source_row,
            text=story,
            language="en",
            document_kind="prose",
            license_reference="mit",
            source_metadata=source_metadata,
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
            doc_id=canonical_source_doc_id("txt360", source_file, source_row),
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
    UltraXUltraFineWebAdapter.ADAPTER_ID: UltraXUltraFineWebAdapter,
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
